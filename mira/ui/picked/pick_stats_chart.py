"""Resume-map chart widget — a CullStats painted in the cull-state
palette (Stage A.2; docs/18 §"Culling contexts").

Custom-painted (precedent: the video timeline, media_canvas), goes
on the face of every Day/Bucket button in the navigator so the user
sees where he is at a glance. Hard rule (frozen 2026-05-17): the
**exact** cull-state palette — discarded = red `{error}`,
compare = orange `{accent}`, kept = green `{success}` — the *same*
tokens as `QLabel#CullStateBar`, sourced from the one resolver
(`mira.ui.theme.resolve_theme_colors`), never ad-hoc, so the
navigator reads in the same visual language as the culler. Untouched =
neutral.

The **tally bar** is the faithful 3-state distribution; the
**badge** carries untouched / in-progress / done (the sparse
journal can't make "untouched" a per-file slice — docs/18). A
tooltip is always set (every control carries a hint).

Geometry + label logic are pure module functions (unit-tested);
`paintEvent` only draws them. Chart *form* (bar now; pie/ring are
eyeball-iterable) is deliberately simple + legible at button size.
"""

from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QColor, QPainter
from PyQt6.QtWidgets import QWidget

from core.cull_stats import (
    BADGE_BROWSED,
    BADGE_DONE,
    BADGE_IN_PROGRESS,
    BADGE_UNTOUCHED,
    CullStats,
)
from mira.ui.i18n import tr

# Cull-state-palette keys → the resolved-theme token they map to
# (the SAME tokens QLabel#CullStateBar uses — single source).
_PALETTE_TOKEN = {
    "picked": "success",
    "candidate": "accent",
    "skipped": "error",
    "untouched": "disabled_text",
}

_BADGE_TEXT = {
    BADGE_UNTOUCHED: "Untouched",
    BADGE_BROWSED: "Browsed",
    BADGE_IN_PROGRESS: "In progress",
    BADGE_DONE: "Done",
}


def cull_state_palette(mode: Optional[str] = None) -> dict[str, QColor]:
    """The four cull-state QColors (kept / candidate / discarded /
    untouched), pulled from the one theme resolver so they are
    byte-identical to QLabel#CullStateBar. ``mode`` defaults to the
    user's active theme; pass it explicitly for deterministic
    tests."""
    if mode not in ("light", "dark"):
        from core.settings import load_settings
        m = str(load_settings().get("theme", "light"))
        mode = m if m in ("light", "dark") else "light"
    from mira.ui.theme import resolve_theme_colors
    resolved = resolve_theme_colors("Mira", mode)
    return {
        key: QColor(resolved[token])
        for key, token in _PALETTE_TOKEN.items()
    }


def bar_segments(stats: CullStats) -> list[tuple[str, float]]:
    """Pure: the stacked-bar fractions for the **faithful 3-state
    tally**, in fixed paint order discard → compare → kept (red →
    orange → green). Empty/zero-total bucket → ``[]`` (the caller
    paints the neutral 'untouched' track). Fractions sum to 1.0."""
    if stats.total <= 0:
        return []
    t = float(stats.total)
    out: list[tuple[str, float]] = []
    for key, n in (
        ("skipped", stats.discarded),
        ("candidate", stats.candidate),
        ("picked", stats.kept),
    ):
        if n > 0:
            out.append((key, n / t))
    return out


def badge_text(stats: CullStats) -> str:
    return tr(_BADGE_TEXT.get(stats.badge, _BADGE_TEXT[BADGE_UNTOUCHED]))


def tooltip_text(stats: CullStats) -> str:
    """Always-present text fallback (every control carries a hint)."""
    if stats.total <= 0:
        return tr("Empty bucket")
    return (
        tr("Picked {k} · Compare {c} · Skip {d}  —  {b}")
        .replace("{k}", str(stats.kept))
        .replace("{c}", str(stats.candidate))
        .replace("{d}", str(stats.discarded))
        .replace("{b}", badge_text(stats))
    )


class PickStatsChart(QWidget):
    """A compact stacked bar + state badge for one ``CullStats``.

    Paints on the face of a navigator Day/Bucket button. Never
    takes focus (the navigator/page owns the keyboard). Call
    :meth:`set_stats`; the tooltip updates with it."""

    _BAR_H = 8
    _PAD = 4

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("PickStatsChart")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._stats: Optional[CullStats] = None
        self._mode: Optional[str] = None        # test override
        self.setMinimumHeight(self._BAR_H + 2 * self._PAD)

    def set_stats(
        self, stats: CullStats, *, mode: Optional[str] = None,
    ) -> None:
        self._stats = stats
        self._mode = mode
        self.setToolTip(tooltip_text(stats))
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(120, self._BAR_H + 2 * self._PAD)

    def paintEvent(self, _event) -> None:  # noqa: N802
        s = self._stats
        if s is None:
            return
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return
        pal = cull_state_palette(self._mode)
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
            bx, bw = self._PAD, max(1, w - 2 * self._PAD)
            by = (h - self._BAR_H) // 2
            # Neutral 'untouched' track underneath (also the whole
            # bar for an untouched/empty bucket — the badge says so).
            p.fillRect(bx, by, bw, self._BAR_H, pal["untouched"])
            x = float(bx)
            for key, frac in bar_segments(s):
                seg = bw * frac
                p.fillRect(
                    int(round(x)), by,
                    max(1, int(round(seg))), self._BAR_H,
                    pal[key],
                )
                x += seg
        finally:
            p.end()
