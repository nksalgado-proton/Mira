"""StatusBreakdown — the honest cull/select decision-state widget (spec/11 §3).

Reassembled into ``mira/ui/`` from the legacy ``ui/base/status_breakdown.py``
(charter §5.2), with the data semantics **corrected** (charter §5.4): the new
``phase_state`` model distinguishes an explicit mark from absence, so this paints the
honest **four-way** distribution — Kept / Candidate / Discarded / **Untouched** — with no
badge-gating heuristic and none of the legacy F-034 time-weighting (counts only; per-clip
durations aren't plumbed into the new model). ``Untouched`` is its own grey row, distinct
from explicit Discarded.

Public API: :meth:`populate(kept, candidate, discarded, untouched, *, title="")`.
Defensive against negative counts (clamped) and a zero-total state (an explicit hint).

:class:`StatusLabels` lets callers override the four row labels (default = Cull vocabulary).
Pass a custom instance to :class:`StatusBreakdown` at construction for Select / Process /
Curate consumers.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtCore import QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PyQt6.QtWidgets import QSizePolicy, QWidget

from mira.ui.i18n import tr

log = logging.getLogger(__name__)


@dataclass
class StatusLabels:
    """Per-consumer row-label override for :class:`StatusBreakdown`.

    Defaults to Cull vocabulary (Kept / Compare / Discarded / Untouched).  Pass a
    custom instance when a different phase uses different terminology.

    ``show_candidate``       — show the Compare/Candidate row (default True).
    ``merge_untouched``      — fold Untouched into the default-action count and omit the
                               Untouched row (default False).  Use when un-decided items have
                               a known fate, so "Untouched = not decided yet but will be X"
                               reads more naturally as X.
    ``merge_untouched_into`` — which row Untouched folds into when ``merge_untouched`` is on:
                               ``'skipped'`` (default) or ``'picked'``.  Set to match the
                               configured per-phase default so the count reflects reality
                               (Nelson 2026-06-03).
    """

    kept: str = "Picked"
    candidate: str = "Compare"
    discarded: str = "Skipped"
    untouched: str = "Untouched"
    show_candidate: bool = True
    merge_untouched: bool = False
    merge_untouched_into: str = "skipped"


# Semantic, fixed-meaning colours (green=kept, orange=candidate, red=discarded,
# grey=untouched) — paint-time constants, not QSS (same exception the legacy used).
_C_KEEP = QColor(0x2E, 0xA0, 0x6B)         # green
_C_CANDIDATE = QColor(0xF3, 0x70, 0x21)    # orange (Compare)
_C_DISCARD = QColor(0xC5, 0x30, 0x30)      # red
_C_UNTOUCHED = QColor(0xB0, 0xB7, 0xBF)    # neutral slate
_C_TRACK = QColor(0, 0, 0, 25)             # bar's unfilled portion

_ROW_FONT_PT = 9.5
_TITLE_FONT_PT = 9.0
_ROW_LABEL_W = 70
_ROW_COUNT_W = 56
_ROW_PCT_W = 44
_BAR_HEIGHT = 10
_ROW_HEIGHT = 22
_ROW_GAP = 4
_PADDING = 8
_TITLE_BOTTOM_GAP = 4
_BAR_TRACK_RADIUS = 3
_MIN_WIDTH = 220


class StatusBreakdown(QWidget):
    """Four-row Kept / Candidate / Discarded / Untouched decision-state widget."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        *,
        labels: Optional[StatusLabels] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("StatusBreakdown")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._labels = labels or StatusLabels()
        self._title = ""
        self._kept = 0
        self._candidate = 0
        self._skipped = 0
        self._untouched = 0
        self.setMinimumWidth(_MIN_WIDTH)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    # ── Public API ───────────────────────────────────────────

    def populate(
        self,
        kept: int,
        candidate: int,
        discarded: int,
        untouched: int,
        *,
        title: str = "",
    ) -> None:
        """Set the four counts + optional title. Negatives clamp to zero; zero-total is a
        valid state (paints an explicit hint)."""
        self._kept = max(0, int(kept))
        self._candidate = max(0, int(candidate))
        self._skipped = max(0, int(discarded))
        self._untouched = max(0, int(untouched))
        self._title = str(title or "")
        self.updateGeometry()
        self.update()
        self._refresh_tooltip()

    # ── Sizing ───────────────────────────────────────────────

    def sizeHint(self) -> QSize:  # noqa: N802
        return self._compute_size()

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return self._compute_size()

    def _compute_size(self) -> QSize:
        n = self._visible_row_count()
        rows_h = _ROW_HEIGHT * n + _ROW_GAP * max(0, n - 1)
        title_h = (
            _row_font_height(_TITLE_FONT_PT, italic=True) + _TITLE_BOTTOM_GAP
            if self._title else 0
        )
        return QSize(_MIN_WIDTH, _PADDING * 2 + title_h + rows_h)

    def _visible_row_count(self) -> int:
        n = 4
        if not self._labels.show_candidate:
            n -= 1
        if self._labels.merge_untouched:
            n -= 1
        return n

    # ── Paint ─────────────────────────────────────────────────

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            self._paint_content(p)
        finally:
            p.end()

    def _paint_content(self, p: QPainter) -> None:
        inner_x = _PADDING
        inner_w = max(_MIN_WIDTH - _PADDING * 2, self.width() - _PADDING * 2)
        y = _PADDING

        if self._title:
            title_font = QFont(p.font())
            title_font.setPointSizeF(_TITLE_FONT_PT)
            title_font.setItalic(True)
            p.setFont(title_font)
            p.setPen(self.palette().mid().color())
            title_h = p.fontMetrics().height()
            p.drawText(
                QRectF(inner_x, y, inner_w, title_h),
                int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter),
                self._title,
            )
            y += title_h + _TITLE_BOTTOM_GAP

        total = self._kept + self._candidate + self._skipped + self._untouched
        if total == 0:
            self._paint_empty(p, inner_x, y, inner_w)
            return

        row_font = QFont(p.font())
        row_font.setPointSizeF(_ROW_FONT_PT)
        row_font.setItalic(False)
        p.setFont(row_font)

        fold_kept = self._labels.merge_untouched and self._labels.merge_untouched_into == "picked"
        fold_disc = self._labels.merge_untouched and not fold_kept
        eff_kept = self._kept + (self._untouched if fold_kept else 0)
        eff_discarded = self._skipped + (self._untouched if fold_disc else 0)
        rows: list = [(tr(self._labels.kept), eff_kept, _C_KEEP)]
        if self._labels.show_candidate:
            rows.append((tr(self._labels.candidate), self._candidate, _C_CANDIDATE))
        rows.append((tr(self._labels.discarded), eff_discarded, _C_DISCARD))
        if not self._labels.merge_untouched:
            rows.append((tr(self._labels.untouched), self._untouched, _C_UNTOUCHED))

        for i, (label, count, color) in enumerate(rows):
            self._paint_row(p, inner_x, y, inner_w, label=label, count=count,
                            total=total, color=color)
            y += _ROW_HEIGHT
            if i < len(rows) - 1:
                y += _ROW_GAP

    def _paint_row(self, p, x, y, w, *, label, count, total, color) -> None:
        label_rect = QRectF(x, y, _ROW_LABEL_W, _ROW_HEIGHT)
        p.setPen(self.palette().windowText().color())
        p.drawText(
            label_rect,
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            label,
        )

        pct_x = x + w - _ROW_PCT_W
        pct = 100.0 * count / max(1, total)
        p.drawText(
            QRectF(pct_x, y, _ROW_PCT_W, _ROW_HEIGHT),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            f"({pct:.0f}%)",
        )
        count_x = pct_x - _ROW_COUNT_W
        p.drawText(
            QRectF(count_x, y, _ROW_COUNT_W - 4, _ROW_HEIGHT),
            int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter),
            f"{count:,}",
        )

        bar_x = x + _ROW_LABEL_W + 8
        bar_w_max = max(20, count_x - bar_x - 8)
        bar_y = y + (_ROW_HEIGHT - _BAR_HEIGHT) / 2.0
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(_C_TRACK)
        p.drawRoundedRect(
            QRectF(bar_x, bar_y, bar_w_max, _BAR_HEIGHT),
            _BAR_TRACK_RADIUS, _BAR_TRACK_RADIUS,
        )
        fill_w = bar_w_max * (count / max(1, total))
        if fill_w >= 1:
            p.setBrush(color)
            p.drawRoundedRect(
                QRectF(bar_x, bar_y, fill_w, _BAR_HEIGHT),
                _BAR_TRACK_RADIUS, _BAR_TRACK_RADIUS,
            )

    def _paint_empty(self, p, x, y, w) -> None:
        empty_font = QFont(p.font())
        empty_font.setPointSizeF(_ROW_FONT_PT)
        empty_font.setItalic(True)
        p.setFont(empty_font)
        p.setPen(self.palette().mid().color())
        rows_h = _ROW_HEIGHT * 4 + _ROW_GAP * 3
        p.drawText(
            QRectF(x, y, w, rows_h),
            int(Qt.AlignmentFlag.AlignCenter),
            tr("(no items)"),
        )

    # ── Tooltip ──────────────────────────────────────────────

    def _refresh_tooltip(self) -> None:
        total = self._kept + self._candidate + self._skipped + self._untouched
        if total == 0:
            self.setToolTip(self._title if self._title else "")
            return
        lines = []
        if self._title:
            lines.append(self._title)
            lines.append("")
        fold_kept = self._labels.merge_untouched and self._labels.merge_untouched_into == "picked"
        fold_disc = self._labels.merge_untouched and not fold_kept
        eff_k = self._kept + (self._untouched if fold_kept else 0)
        eff_d = self._skipped + (self._untouched if fold_disc else 0)
        parts = [f"{tr(self._labels.kept)}: {eff_k:,}"]
        if self._labels.show_candidate:
            parts.append(f"{tr(self._labels.candidate)}: {self._candidate:,}")
        parts.append(f"{tr(self._labels.discarded)}: {eff_d:,}")
        if not self._labels.merge_untouched:
            parts.append(f"{tr(self._labels.untouched)}: {self._untouched:,}")
        lines.append("  ·  ".join(parts))
        lines.append(tr("Total: {t}").replace("{t}", f"{total:,}"))
        self.setToolTip("\n".join(lines))


def _row_font_height(pt: float, *, italic: bool = False) -> int:
    font = QFont()
    font.setPointSizeF(pt)
    font.setItalic(italic)
    return QFontMetrics(font).height()
