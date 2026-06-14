"""Reusable resume-map list button (Stage A, eyeball-tuned form —
docs/18 §"The bucket navigator is a RESUME MAP").

One class for every successive-selection list in the culling flow
(the 4-context chooser, the Day list, the Bucket list) — "one
class, level-aware slots". Fixed **four-line** layout (Nelson
eyeball 2026-05-18) so a row reads as a small data card:

  ┌────────────────────────────────────────────────────────────┐
  │ TITLE (primary, bold)                        [STATE BADGE]  │  L1
  │ Styles:  wildlife-static 88 · landscape 42 …                │  L2
  │ 21 Buckets:  19 burst · 1 individual · 1 video              │  L3
  │ Status:  ▰▰▰▰▰▰▰▱▱▱   ← bounded tally, palette, no numbers   │  L4
  └────────────────────────────────────────────────────────────┘

Lines 2-3 are ``(qualifier, value)`` pairs: the **qualifier is
bold** ("Styles:", "N Buckets:", "Camera:"), the value regular —
fewer than two pairs leaves the spare line blank. Line 4 is the
literal bold "Status:" followed by the cull-tally bar and **nothing
else** (Nelson 2026-05-18: drop the "x / N kept" text — the bar is
the status; the exact Kept/Comp/Disc breakdown stays in the
always-present tooltip, the frozen text fallback).

The tally bar keeps the **exact cull-state palette** (frozen hard
constraint — byte-identical to ``QLabel#CullStateBar`` via the one
resolver) and stays a **bounded** slot, never edge-to-edge. Height
lives in QSS (`QPushButton#ListButton`) — a styled QPushButton is
re-polished by QStyleSheetStyle, which clobbers a Python height.
"""

from __future__ import annotations

from typing import Optional, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QCursor, QFont, QPainter
from PyQt6.QtWidgets import QPushButton, QWidget

from core.cull_stats import (
    BADGE_BROWSED,
    BADGE_DONE,
    BADGE_IN_PROGRESS,
    CullStats,
)
from mira.ui.picked.pick_stats_chart import (
    badge_text,
    bar_segments,
    cull_state_palette,
    tooltip_text,
)
from mira.ui.i18n import tr

# Badge → resolved-theme token (NOT the cull palette — that is
# reserved for the tally so the two never read as the same thing).
_BADGE_TOKEN = {
    BADGE_DONE: "success",          # green — declared done
    BADGE_IN_PROGRESS: "accent",    # orange — actively culling
    BADGE_BROWSED: "primary",       # blue — opened, not yet acted
}                                   # else (untouched) → disabled_text
#                                     (grey — never opened)

_PAD = 14
_BAR_H = 11
_BAR_MAX_W = 420                    # the bounded slot — never full width
_LINE_GAP = 6

Row = tuple[str, str]               # (bold qualifier, regular value)


def _badge_color(badge: str, mode: Optional[str] = None) -> QColor:
    if mode not in ("light", "dark"):
        from core.settings import load_settings
        m = str(load_settings().get("theme", "light"))
        mode = m if m in ("light", "dark") else "light"
    from mira.ui.theme import resolve_theme_colors
    resolved = resolve_theme_colors("Mira", mode)
    return QColor(resolved[_BADGE_TOKEN.get(badge, "disabled_text")])


class ListButton(QPushButton):
    """A resume-map row. ``title`` = the primary line (caller builds
    it per level); ``rows`` = up to two ``(qualifier, value)`` pairs
    for the two counter lines (qualifier rendered bold); ``stats`` =
    the faithful 3-state ``CullStats`` for the bounded tally + badge.
    ``note`` is appended to the always-present tooltip (e.g.
    provenance). ``mode`` overrides the theme for deterministic
    tests.

    **Level-aware slots (frozen docs/18): ``stats=None`` → the
    no-status COMPACT mode** — a level with no cull progress (the
    4-context chooser: cameras / phone / other / home is a *picker*,
    not a cull unit). It renders title + the qualifier rows ONLY:
    no badge chip, no ``Status:`` bar; shorter (the
    ``#ListButtonCompact`` QSS role).

    **Export action slot (frozen docs/18 §"Export reach", Stage C
    inc.4b).** A small child Export button at the right edge —
    accepted tweak to the otherwise-frozen card. It is a *child
    widget over the button*, so clicking it fires
    :attr:`export_requested` and **not** the row-open ``clicked``
    (Qt routes the press to the topmost child). Hidden by default;
    the navigator calls :meth:`set_export` only on Day/Bucket rows
    that have ≥1 Kept in scope — never in COMPACT (a picker has
    nothing to export)."""

    export_requested = pyqtSignal()

    def __init__(
        self,
        title: str,
        rows: Sequence[Row],
        stats: Optional[CullStats] = None,
        *,
        note: str = "",
        mode: Optional[str] = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(
            "ListButton" if stats is not None else "ListButtonCompact"
        )                                       # height via QSS
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self._title = title
        self._rows = list(rows)[:2]
        self._stats = stats
        self._mode = mode
        self._set_tip(stats, note)

        # Export slot — created once, hidden until set_export(True).
        # COMPACT (picker) rows never get one.
        self._export_btn: Optional[QPushButton] = None
        if stats is not None:
            b = QPushButton(tr("Export"), self)
            b.setObjectName("ListButtonExport")
            b.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setToolTip(tr(
                "Export the Kept photos here, into "
                "Day / Style sub-folders. Repeatable; sources are "
                "never touched."
            ))
            b.clicked.connect(self.export_requested.emit)
            b.setVisible(False)
            self._export_btn = b

    def set_export(self, enabled: bool) -> None:
        """Show/hide the Export action (frozen: disabled at any level
        whose in-scope Kept set is empty). No-op for COMPACT rows."""
        if self._export_btn is not None:
            self._export_btn.setVisible(bool(enabled))
            self._position_export()

    def _position_export(self) -> None:
        b = self._export_btn
        # ``isHidden()`` reflects the explicit setVisible(False)
        # state only — NOT the parent-chain visibility (which is what
        # ``isVisible()`` reports). The cull/process navigator calls
        # ``set_export(True)`` on every Day card BEFORE the scroll
        # area is shown, so ``isVisible()`` would falsely return False
        # and the position computation would be skipped — leaving the
        # button at its (0, 0) default, i.e. top-left of the card
        # (Nelson 2026-05-21 bug). ``isHidden()`` answers the right
        # question: "did the user / caller hide this button?".
        if b is None or b.isHidden():
            return
        b.adjustSize()
        bw, bh = b.width(), b.height()
        # Bottom-right (Nelson 2026-05-21): the previous right-edge-
        # middle placement sat on top of the qualifier text rows AND
        # crowded the top-right corner against the badge chip + title.
        # Bottom-right is its own quiet quadrant — clear of the title
        # (top-left), the badge chip (top-right), the qualifier rows
        # (middle, can now extend full-width), and the Status tally
        # bar (bottom-left, capped at _BAR_MAX_W).
        b.move(max(0, self.width() - _PAD - bw),
               max(0, self.height() - _PAD - bh))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._position_export()

    def showEvent(self, event) -> None:  # noqa: N802
        """Re-position the export button on the first show — the
        widget's geometry is only finalised here, so this is the
        moment ``self.width()/.height()`` reflect the real laid-out
        size (Nelson 2026-05-21 — without this, day cards
        constructed before the scroll area was shown landed the
        Export button at (0, 0))."""
        super().showEvent(event)
        self._position_export()

    def _set_tip(self, stats: Optional[CullStats], note: str) -> None:
        tip = tooltip_text(stats) if stats is not None else ""
        if tip and note:
            self.setToolTip(f"{tip}\n{note}")
        else:
            self.setToolTip(tip or note)

    def set_data(
        self,
        title: str,
        rows: Sequence[Row],
        stats: Optional[CullStats] = None,
        *,
        note: str = "",
    ) -> None:
        """Update title / rows / stats / tooltip in one call.

        ``stats=None`` is the COMPACT-mode path (Curate pass cards,
        cull-context chooser): no badge + no tally bar drawn, just
        the title + the two row pairs. Backward compatible — every
        cull-flow caller passes a real CullStats and unchanged.
        """
        self._title = title
        self._rows = list(rows)[:2]
        self._stats = stats
        self._set_tip(stats, note)
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        super().paintEvent(event)         # button chrome + states
        s = self._stats
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return
        pal = cull_state_palette(self._mode)
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            base = self.palette().buttonText().color()
            sec = QColor(base)
            sec.setAlpha(175)
            fm = p.fontMetrics()
            lh = fm.height()
            regular = QFont(p.font())
            bold = QFont(regular)
            bold.setBold(True)

            def _qual(x: int, y: int, qual: str, val: str) -> None:
                """Bold 'Qual:' then regular value, elided to width."""
                if not qual and not val:
                    return
                cx = x
                if qual:
                    p.setFont(bold)
                    p.setPen(base)
                    q = f"{qual}:  "
                    p.drawText(cx, y, q)
                    cx += p.fontMetrics().horizontalAdvance(q)
                p.setFont(regular)
                p.setPen(sec)
                p.drawText(
                    cx, y,
                    fm.elidedText(val, Qt.TextElideMode.ElideRight,
                                  max(10, w - _PAD - cx)),
                )

            compact = s is None       # no-status level (context picker)

            # ── L1: title (bold, left) + badge chip (right) ────────
            title_max = w - 2 * _PAD
            if not compact:
                badge = badge_text(s)
                bcol = _badge_color(s.badge, self._mode)
                chip_pad = 8
                chip_w = fm.horizontalAdvance(badge) + 2 * chip_pad
                chip_h = lh + 4
                chip_x = w - _PAD - chip_w
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(bcol.red(), bcol.green(),
                                  bcol.blue(), 38))
                p.drawRoundedRect(chip_x, _PAD, chip_w, chip_h, 6, 6)
                p.setPen(bcol)
                p.drawText(chip_x + chip_pad,
                           _PAD + fm.ascent() + 2, badge)
                title_max = chip_x - 12 - _PAD

            y = _PAD + fm.ascent()
            p.setFont(bold)
            p.setPen(base)
            p.drawText(_PAD, y, fm.elidedText(
                self._title, Qt.TextElideMode.ElideRight,
                max(10, title_max)))

            # ── L2 / L3: bold-qualifier counter lines ──────────────
            step = lh + _LINE_GAP
            for i in range(2):
                y += step
                q, v = self._rows[i] if i < len(self._rows) else ("", "")
                _qual(_PAD, y, q, v)

            if compact:               # no Status bar for a picker
                return

            # ── L4: bold "Status:" + the bounded tally bar only ────
            p.setFont(bold)
            p.setPen(base)
            lbl = tr("Status:") + "  "
            sy = h - _PAD - max(_BAR_H, lh) + fm.ascent()
            p.drawText(_PAD, sy, lbl)
            bx = _PAD + p.fontMetrics().horizontalAdvance(lbl)
            by = h - _PAD - _BAR_H
            bw = min(w - _PAD - bx, _BAR_MAX_W)
            if bw > 4:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(pal["untouched"])
                p.drawRect(bx, by, bw, _BAR_H)
                x = float(bx)
                for key, frac in bar_segments(s):
                    seg = bw * frac
                    p.setBrush(pal[key])
                    p.drawRect(int(round(x)), by,
                               max(1, int(round(seg))), _BAR_H)
                    x += seg
        finally:
            p.end()
