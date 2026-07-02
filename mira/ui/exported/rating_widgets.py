"""spec/159 — purpose-built rating controls for the review dialog.

Custom-painted ``QWidget`` controls. No ``setStyleSheet`` anywhere — each
widget paints itself in ``paintEvent`` so the visual treatment is
self-contained, theme-stable, and clears the QSS guard (spec/92 §7).

Three widgets:

* :class:`StarRow` — five star polygons; click the Nth → 1..N stars;
  click the already-filled Nth → clear (LRC convention).
* :class:`ColorLabelRow` — five rounded swatches in the LRC palette; the
  active one carries a white halo + check.
* :class:`FlagToggle` — a flag glyph (pole + triangular cloth) that
  toggles between outline (off) and saturated amber (on).
* :class:`DeleteToggle` — a danger-pill toggle ("Mark for deletion" ⇄
  "Marked for deletion").

All controls accept a :class:`~typing.Callable` via ``setValue`` and emit
the matching signal on user input. Hover state is read in
``enterEvent``/``leaveEvent`` so the paint can brighten without QSS.
"""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import QApplication, QComboBox, QSizePolicy, QWidget

from mira.ui.i18n import tr
from mira.ui.palette import PALETTE


def _theme_palette() -> dict[str, str]:
    """Return the active theme's palette dict. Used inside paintEvents
    so the rating widgets read correctly on both dark + light themes
    (Nelson 2026-06-30 round 2 — DeleteToggle label was illegible on
    light because the off-state ink was hardcoded for dark)."""
    app = QApplication.instance()
    mode = (app.property("theme") if app else None) or "dark"
    return PALETTE.get(mode, PALETTE["dark"])


_MEANINGS_CACHE: dict[str, str] | None = None


def _load_rating_meanings() -> dict[str, str]:
    """Return the user's per-rating meanings, cached at module level so
    a grid of 1 000 thumbs doesn't hit the settings JSON 1 000 times.
    Silent fallback to ``{}`` when settings are unavailable (e.g. first
    launch before the wizard writes settings.json). Call
    :func:`invalidate_rating_meanings_cache` to force a reload after
    the user applies a settings change."""
    global _MEANINGS_CACHE
    if _MEANINGS_CACHE is None:
        try:
            from mira.settings.repo import SettingsRepo
            _MEANINGS_CACHE = dict(
                SettingsRepo().load().rating_meanings or {})
        except Exception:                                   # noqa: BLE001
            _MEANINGS_CACHE = {}
    return _MEANINGS_CACHE


def invalidate_rating_meanings_cache() -> None:
    """Drop the cached meanings dict — next widget-side tooltip refresh
    reads the on-disk settings again. Called from
    :class:`~mira.ui.base.settings_dialog.SettingsDialog` after Apply
    / Reset so an in-flight review dialog picks up the fresh labels
    without a restart."""
    global _MEANINGS_CACHE
    _MEANINGS_CACHE = None


def _compose_meaning_tooltip(
    meanings: dict[str, str],
    header: str,
    meaning_key: str,
    category_key: str,
) -> str:
    """Format the standard hover tooltip: ``<header> — <meaning>
    (<category>)`` with empty parts omitted so we never render a bare
    dash or trailing parens."""
    meaning = (meanings.get(meaning_key) or "").strip()
    category = (meanings.get(category_key) or "").strip()
    text = header
    if meaning:
        text = f"{text} — {meaning}"
    if category:
        text = f"{text}  ({category})"
    return text

# ── colour vocabulary (matches the Thumb cell-chrome palette) ─────────

#: LRC label hex values — kept in sync with
#: :data:`mira.ui.design.thumbs._COLOR_LABEL_HEX`.
COLOR_LABEL_HEX: dict[str, str] = {
    "red":    "#D9382E",
    "yellow": "#E4B91F",
    "green":  "#2DA84A",
    "blue":   "#3A8DD8",
    "purple": "#9C4DC9",
}
#: The Shift+1..5 keyboard order (matches the review dialog spec).
COLOR_LABEL_ORDER: tuple[str, ...] = (
    "red", "yellow", "green", "blue", "purple",
)

_STAR_GOLD = QColor("#F2C84A")
_STAR_GOLD_DARK = QColor("#B88A1E")
_FLAG_AMBER = QColor("#F5B042")
_DELETE_RED = QColor("#A02020")
_DELETE_RED_DIM = QColor("#7A1818")
_DELETE_HOVER = QColor("#E66060")
_PREFERRED_GREEN = QColor("#2DA84A")
_PREFERRED_GREEN_DARK = QColor("#1F7A36")
_PREFERRED_HOVER = QColor("#4FBF6A")


# ── helpers ───────────────────────────────────────────────────────────

def _star_polygon(cx: float, cy: float, r_outer: float) -> QPainterPath:
    """Classic 5-point star polygon centred at (cx, cy).

    ``r_outer`` is the circumscribed radius; ``r_inner`` is fixed at
    ~0.42 of ``r_outer`` (a slightly chunkier-than-textbook star reads
    better at button-sized targets)."""
    from math import cos, pi, sin

    r_inner = r_outer * 0.42
    path = QPainterPath()
    # Start at the top point (12 o'clock).
    for i in range(10):
        angle = -pi / 2 + i * pi / 5
        r = r_outer if i % 2 == 0 else r_inner
        x = cx + r * cos(angle)
        y = cy + r * sin(angle)
        if i == 0:
            path.moveTo(x, y)
        else:
            path.lineTo(x, y)
    path.closeSubpath()
    return path


def _checkmark_path(rect: QRectF) -> QPainterPath:
    """Thin centred check mark inside ``rect`` (used by ColorLabelRow)."""
    p = QPainterPath()
    pad = rect.width() * 0.22
    p.moveTo(rect.left() + pad,        rect.center().y())
    p.lineTo(rect.center().x() - pad/2, rect.bottom() - pad)
    p.lineTo(rect.right() - pad,       rect.top() + pad)
    return p


# ── StarRow ───────────────────────────────────────────────────────────

class StarRow(QWidget):
    """Row of five clickable stars.

    Click the Nth → set 1..N filled. Click the already-Nth → clear all
    (LRC convention). Hover highlights the Nth + every star to its left
    (a "preview" cue so the user reads what they're about to commit).

    The widget reads its current value via :meth:`value` and writes it
    via :meth:`setValue`; pointer input always goes through
    :meth:`_set_value` so :data:`value_changed` fires exactly once per
    distinct user action.
    """

    #: ``stars`` is ``1..5`` or ``None`` (cleared).
    value_changed = pyqtSignal(object)

    _STAR_PX = 30
    _GAP_PX = 4
    _PAD_PX = 4

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("StarRow")
        self._stars: Optional[int] = None
        self._hover_n: int = 0     # 0 = no hover
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(self._STAR_PX + self._PAD_PX * 2)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    # ── value ──────────────────────────────────────────────────────

    def value(self) -> Optional[int]:
        return self._stars

    def setValue(self, stars: Optional[int]) -> None:
        """Set the value without emitting (used by external rehydrate)."""
        new_val = stars if (stars is None or 1 <= int(stars) <= 5) else None
        if new_val != self._stars:
            self._stars = None if new_val is None else int(new_val)
            self.update()

    def _set_value(self, stars: Optional[int]) -> None:
        """User-input mutation: store + emit + repaint."""
        if stars == self._stars:
            return
        self._stars = None if stars is None else int(stars)
        self.value_changed.emit(self._stars)
        self.update()

    # ── geometry ───────────────────────────────────────────────────

    def sizeHint(self) -> QSize:  # noqa: N802 — Qt
        w = self._PAD_PX * 2 + self._STAR_PX * 5 + self._GAP_PX * 4
        h = self._PAD_PX * 2 + self._STAR_PX
        return QSize(w, h)

    def _star_rect(self, n: int) -> QRectF:
        """Hit-rect of the ``n``-th star (n=1..5)."""
        left = self._PAD_PX + (n - 1) * (self._STAR_PX + self._GAP_PX)
        return QRectF(left, self._PAD_PX, self._STAR_PX, self._STAR_PX)

    def _star_at(self, x: float) -> int:
        """Star index 1..5 at horizontal coord ``x``; 0 if outside."""
        for n in range(1, 6):
            if self._star_rect(n).contains(QPointF(x, self._PAD_PX + 1)):
                return n
        return 0

    # ── input ─────────────────────────────────────────────────────

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        n = self._star_at(ev.position().x())
        if n != self._hover_n:
            self._hover_n = n
            self._refresh_tooltip(n)
            self.update()

    def leaveEvent(self, _ev) -> None:  # noqa: N802
        if self._hover_n != 0:
            self._hover_n = 0
            self.update()

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        n = self._star_at(ev.position().x())
        if n == 0:
            return
        # LRC convention: click the already-Nth star → clear all.
        if self._stars == n:
            self._set_value(None)
        else:
            self._set_value(n)

    def _refresh_tooltip(self, n: int) -> None:
        """Per-star tooltip carrying the user's own meaning + category
        tag (e.g. "3 stars — Excellent  (The Quality)")."""
        if n == 0:
            self.setToolTip("")
            return
        header = tr("1 star") if n == 1 else tr("N stars").replace("N", str(n))
        self.setToolTip(_compose_meaning_tooltip(
            _load_rating_meanings(), header,
            f"stars_{n}", "category_stars"))

    # ── paint ─────────────────────────────────────────────────────

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = _theme_palette()
        ink_off = QColor(pal["ink_faint"])
        ink_dim = QColor(pal["ink_soft"])
        active = self._stars or 0
        hover = self._hover_n
        for n in range(1, 6):
            rect = self._star_rect(n)
            cx = rect.center().x()
            cy = rect.center().y()
            r = self._STAR_PX / 2 - 2
            path = _star_polygon(cx, cy, r)
            filled = n <= active
            hover_preview = (hover > 0) and (n <= hover) and (not filled)
            if filled:
                p.setBrush(QBrush(_STAR_GOLD))
                p.setPen(QPen(_STAR_GOLD_DARK, 1.4))
            elif hover_preview:
                p.setBrush(QBrush(QColor(242, 200, 74, 70)))
                p.setPen(QPen(_STAR_GOLD, 1.4))
            else:
                p.setBrush(Qt.BrushStyle.NoBrush)
                colour = ink_dim if hover == n else ink_off
                p.setPen(QPen(colour, 1.4))
            p.drawPath(path)
        p.end()


# ── ColorLabelRow ─────────────────────────────────────────────────────

class ColorLabelRow(QWidget):
    """Row of five LRC colour swatches.

    Active swatch carries a white halo + check. Click an already-active
    swatch → clear. Hovered swatches brighten and pop slightly.
    """

    #: emits the label string (``'red'`` etc.) or ``None``.
    value_changed = pyqtSignal(object)

    _SW_PX = 30
    _GAP_PX = 6
    _PAD_PX = 4

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ColorLabelRow")
        self._value: Optional[str] = None
        self._hover_key: Optional[str] = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def value(self) -> Optional[str]:
        return self._value

    def setValue(self, label: Optional[str]) -> None:
        if label is not None and label not in COLOR_LABEL_HEX:
            label = None
        if label != self._value:
            self._value = label
            self.update()

    def _set_value(self, label: Optional[str]) -> None:
        if label == self._value:
            return
        self._value = label
        self.value_changed.emit(label)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        n = len(COLOR_LABEL_ORDER)
        w = self._PAD_PX * 2 + self._SW_PX * n + self._GAP_PX * (n - 1)
        h = self._PAD_PX * 2 + self._SW_PX
        return QSize(w, h)

    def _swatch_rect(self, idx: int) -> QRectF:
        left = self._PAD_PX + idx * (self._SW_PX + self._GAP_PX)
        return QRectF(left, self._PAD_PX, self._SW_PX, self._SW_PX)

    def _key_at(self, x: float) -> Optional[str]:
        for i, key in enumerate(COLOR_LABEL_ORDER):
            if self._swatch_rect(i).contains(QPointF(x, self._PAD_PX + 1)):
                return key
        return None

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        key = self._key_at(ev.position().x())
        if key != self._hover_key:
            self._hover_key = key
            self._refresh_tooltip(key)
            self.update()

    def leaveEvent(self, _ev) -> None:  # noqa: N802
        if self._hover_key is not None:
            self._hover_key = None
            self.update()

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        key = self._key_at(ev.position().x())
        if key is None:
            return
        if key == self._value:
            self._set_value(None)
        else:
            self._set_value(key)

    def _refresh_tooltip(self, key: Optional[str]) -> None:
        """Per-swatch tooltip: '<Colour> — <meaning>  (<category>)'."""
        if key is None:
            self.setToolTip("")
            return
        header = tr(key.title())
        self.setToolTip(_compose_meaning_tooltip(
            _load_rating_meanings(), header,
            f"color_{key}", "category_color"))

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, key in enumerate(COLOR_LABEL_ORDER):
            r = self._swatch_rect(i)
            colour = QColor(COLOR_LABEL_HEX[key])
            is_active = (self._value == key)
            is_hover = (self._hover_key == key) and not is_active
            # Shrink slightly when hovered (active swatch keeps full size
            # but gets the halo — the white halo *is* the size difference).
            sw_rect = QRectF(r)
            if is_hover:
                sw_rect.adjust(-1, -1, 1, 1)
            p.setBrush(QBrush(colour))
            p.setPen(QPen(QColor(0, 0, 0, 110), 1))
            p.drawRoundedRect(sw_rect, 6, 6)
            if is_active:
                # White outer halo.
                halo = QRectF(r).adjusted(-3, -3, 3, 3)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("#ffffff"), 2.4))
                p.drawRoundedRect(halo, 8, 8)
                # Inner check mark.
                p.setPen(QPen(QColor("#ffffff"), 2.6,
                              Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap,
                              Qt.PenJoinStyle.RoundJoin))
                p.drawPath(_checkmark_path(r))
        p.end()


# ── ColorLabelMultiRow ───────────────────────────────────────────────


class ColorLabelMultiRow(QWidget):
    """Multi-select sibling of :class:`ColorLabelRow`.

    Used by the §4.5 filter bar: the user picks ANY subset of the five
    LRC labels, then the cell list narrows to lineage rows whose label
    is in the chosen set. Selected swatches paint with the white halo
    + check; unselected swatches stay flat. Click toggles.
    """

    #: ``set[str]`` — emits a fresh snapshot of the selection.
    value_changed = pyqtSignal(set)

    _SW_PX = 26
    _GAP_PX = 5
    _PAD_PX = 3

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ColorLabelMultiRow")
        self._value: set = set()
        self._hover_key: Optional[str] = None
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def value(self) -> set:
        return set(self._value)

    def setValue(self, labels) -> None:
        """Push a set without emitting (used by the host to seed
        state on construction or after :meth:`reset`)."""
        new_val = {
            k for k in labels or () if k in COLOR_LABEL_HEX}
        if new_val != self._value:
            self._value = new_val
            self.update()

    def _toggle(self, key: str) -> None:
        if key in self._value:
            self._value.discard(key)
        else:
            self._value.add(key)
        self.value_changed.emit(set(self._value))
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        n = len(COLOR_LABEL_ORDER)
        w = self._PAD_PX * 2 + self._SW_PX * n + self._GAP_PX * (n - 1)
        h = self._PAD_PX * 2 + self._SW_PX
        return QSize(w, h)

    def _swatch_rect(self, idx: int) -> QRectF:
        left = self._PAD_PX + idx * (self._SW_PX + self._GAP_PX)
        return QRectF(left, self._PAD_PX, self._SW_PX, self._SW_PX)

    def _key_at(self, x: float) -> Optional[str]:
        for i, key in enumerate(COLOR_LABEL_ORDER):
            if self._swatch_rect(i).contains(QPointF(x, self._PAD_PX + 1)):
                return key
        return None

    def mouseMoveEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        key = self._key_at(ev.position().x())
        if key != self._hover_key:
            self._hover_key = key
            self._refresh_tooltip(key)
            self.update()

    def leaveEvent(self, _ev) -> None:  # noqa: N802
        if self._hover_key is not None:
            self._hover_key = None
            self.update()

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() != Qt.MouseButton.LeftButton:
            return
        key = self._key_at(ev.position().x())
        if key is None:
            return
        self._toggle(key)

    def _refresh_tooltip(self, key: Optional[str]) -> None:
        """Per-swatch tooltip carrying the user's meaning + category tag,
        so the filter-bar swatch reads as, e.g., ``Red — World Trips
        (The Subject)`` on hover."""
        if key is None:
            self.setToolTip("")
            return
        header = tr(key.title())
        self.setToolTip(_compose_meaning_tooltip(
            _load_rating_meanings(), header,
            f"color_{key}", "category_color"))

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        for i, key in enumerate(COLOR_LABEL_ORDER):
            r = self._swatch_rect(i)
            colour = QColor(COLOR_LABEL_HEX[key])
            is_active = (key in self._value)
            is_hover = (self._hover_key == key) and not is_active
            sw_rect = QRectF(r)
            if is_hover:
                sw_rect.adjust(-1, -1, 1, 1)
            p.setBrush(QBrush(colour))
            p.setPen(QPen(QColor(0, 0, 0, 110), 1))
            p.drawRoundedRect(sw_rect, 5, 5)
            if is_active:
                halo = QRectF(r).adjusted(-2, -2, 2, 2)
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.setPen(QPen(QColor("#ffffff"), 2.2))
                p.drawRoundedRect(halo, 7, 7)
                p.setPen(QPen(QColor("#ffffff"), 2.4,
                              Qt.PenStyle.SolidLine,
                              Qt.PenCapStyle.RoundCap,
                              Qt.PenJoinStyle.RoundJoin))
                p.drawPath(_checkmark_path(r))
        p.end()


# ── FlagToggle ────────────────────────────────────────────────────────

class FlagToggle(QWidget):
    """A real flag glyph that toggles between off (outline) and on
    (saturated amber). Clickable; emits :data:`toggled` on user action.
    """

    toggled = pyqtSignal(bool)

    _SIZE_PX = 36
    _PAD_PX = 4

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("FlagToggle")
        self._on: bool = False
        self._hover: bool = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._refresh_tooltip()
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def value(self) -> bool:
        return self._on

    def setValue(self, on: bool) -> None:
        on = bool(on)
        if on != self._on:
            self._on = on
            self._refresh_tooltip()
            self.update()

    def _set_value(self, on: bool) -> None:
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        self._refresh_tooltip()
        self.toggled.emit(on)
        self.update()

    def _refresh_tooltip(self) -> None:
        """Tooltip: 'Portfolio flag (K) — <meaning>  (<category>)'.
        Meaning reflects the current on/off state."""
        header = tr("Portfolio flag (K)")
        meaning_key = "flag_on" if self._on else "flag_off"
        self.setToolTip(_compose_meaning_tooltip(
            _load_rating_meanings(), header,
            meaning_key, "category_flag"))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self._SIZE_PX, self._SIZE_PX)

    def enterEvent(self, _ev) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, _ev) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._set_value(not self._on)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        # The pole sits one-third in from the left; the cloth flies
        # out to the right. Pole 1.6 px wide, full-height.
        pad = self._PAD_PX
        size = self._SIZE_PX - pad * 2
        x = pad + size * 0.30        # pole x
        y = pad + size * 0.06        # top of pole
        pole_h = size * 0.86
        # Body colour state — pulled off the active theme so the
        # off-state ink reads on both dark and light themes.
        pal = _theme_palette()
        ink_faint = QColor(pal["ink_faint"])
        if self._on:
            cloth_fill = QColor(_FLAG_AMBER)
            cloth_pen = QPen(QColor("#7A4A12"), 1.4)
            # Pole matches the cloth so the glyph reads as one object
            # (Nelson 2026-06-30 round 3).
            pole_pen = QPen(_FLAG_AMBER, 2.0)
        elif self._hover:
            cloth_fill = QColor(245, 176, 66, 60)
            cloth_pen = QPen(_FLAG_AMBER, 1.6)
            pole_pen = QPen(_FLAG_AMBER, 1.8)
        else:
            cloth_fill = QColor(0, 0, 0, 0)
            cloth_pen = QPen(ink_faint, 1.6)
            pole_pen = QPen(ink_faint, 1.8)
        # Cloth — pennant shape with a forked tail (so it reads as a
        # *flag*, not a triangle). Anchored to the pole at the top.
        path = QPainterPath()
        cloth_w = size * 0.58
        cloth_h = size * 0.50
        tx = x
        ty = y + 1
        path.moveTo(tx, ty)
        path.lineTo(tx + cloth_w,     ty + cloth_h * 0.25)
        path.lineTo(tx + cloth_w * 0.78, ty + cloth_h * 0.50)
        path.lineTo(tx + cloth_w,     ty + cloth_h * 0.78)
        path.lineTo(tx,               ty + cloth_h * 0.85)
        path.closeSubpath()
        p.setBrush(QBrush(cloth_fill))
        p.setPen(cloth_pen)
        p.drawPath(path)
        # Pole on top of the cloth so the join is clean.
        p.setPen(pole_pen)
        p.drawLine(QPointF(x, y), QPointF(x, y + pole_h))
        # Finial dot at the top of the pole.
        p.setBrush(QBrush(pole_pen.color()))
        p.drawEllipse(QPointF(x, y), 1.6, 1.6)
        p.end()


# ── DeleteToggle ──────────────────────────────────────────────────────

class DeleteToggle(QWidget):
    """A danger-pill toggle. Off: ghost outline + dim label
    ("⌫ Mark for deletion"). On: solid danger-red bg + white bold
    label ("✓ Marked for deletion"). Click toggles.
    """

    toggled = pyqtSignal(bool)

    _HEIGHT_PX = 32

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("DeleteToggle")
        self._on: bool = False
        self._hover: bool = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(self._HEIGHT_PX)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setToolTip(tr("Mark for deletion (D)"))

    def value(self) -> bool:
        return self._on

    def setValue(self, on: bool) -> None:
        on = bool(on)
        if on != self._on:
            self._on = on
            self.updateGeometry()
            self.update()

    def _set_value(self, on: bool) -> None:
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        self.toggled.emit(on)
        self.updateGeometry()
        self.update()

    def _label(self) -> str:
        return (tr("✓ Marked for deletion")
                if self._on else tr("⌫ Mark for deletion"))

    def sizeHint(self) -> QSize:  # noqa: N802
        f = self.font()
        f.setBold(self._on)
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self._label()) + 28
        return QSize(w, self._HEIGHT_PX)

    def enterEvent(self, _ev) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, _ev) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._set_value(not self._on)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        r = rect.height() / 2
        pal = _theme_palette()
        if self._on:
            p.setBrush(QBrush(_DELETE_RED))
            p.setPen(QPen(_DELETE_RED_DIM, 1.4))
            text_color = QColor("#ffffff")
            bold = True
        elif self._hover:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_DELETE_HOVER, 1.6))
            text_color = _DELETE_HOVER
            bold = False
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            # Off-state: theme-aware border + primary ink for the
            # label so the text reads on both dark + light themes.
            p.setPen(QPen(QColor(pal["card_border"]), 1.2))
            text_color = QColor(pal["ink"])
            bold = False
        p.drawRoundedRect(rect, r, r)
        # Label
        f = QFont(self.font())
        f.setBold(bold)
        p.setFont(f)
        p.setPen(text_color)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self._label())
        p.end()


# ── PreferredToggle ──────────────────────────────────────────────────


class PreferredToggle(QWidget):
    """spec/159 §6+ — a pill toggle that marks "this is the chosen
    version of the shot."

    Off: ghost outline + dim "✓ Use this" label.
    On:  solid green bg + white bold "✓ Preferred" label.

    Hidden by callers when the lineage row has no siblings (single-
    version cells are implicitly preferred). Click toggles."""

    toggled = pyqtSignal(bool)

    _HEIGHT_PX = 32

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("PreferredToggle")
        self._on: bool = False
        self._hover: bool = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(self._HEIGHT_PX)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setToolTip(tr(
            "Use this version (downstream Cuts default to the "
            "preferred version of each shot)."))

    def value(self) -> bool:
        return self._on

    def setValue(self, on: bool) -> None:
        on = bool(on)
        if on != self._on:
            self._on = on
            self.updateGeometry()
            self.update()

    def _set_value(self, on: bool) -> None:
        on = bool(on)
        if on == self._on:
            return
        self._on = on
        self.toggled.emit(on)
        self.updateGeometry()
        self.update()

    def _label(self) -> str:
        return (tr("✓ Preferred")
                if self._on else tr("✓ Use this"))

    def sizeHint(self) -> QSize:  # noqa: N802
        fm = self.fontMetrics()
        w = fm.horizontalAdvance(self._label()) + 28
        return QSize(w, self._HEIGHT_PX)

    def enterEvent(self, _ev) -> None:  # noqa: N802
        self._hover = True
        self.update()

    def leaveEvent(self, _ev) -> None:  # noqa: N802
        self._hover = False
        self.update()

    def mousePressEvent(self, ev: QMouseEvent) -> None:  # noqa: N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._set_value(not self._on)

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        r = rect.height() / 2
        pal = _theme_palette()
        if self._on:
            p.setBrush(QBrush(_PREFERRED_GREEN))
            p.setPen(QPen(_PREFERRED_GREEN_DARK, 1.4))
            text_color = QColor("#ffffff")
            bold = True
        elif self._hover:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(_PREFERRED_HOVER, 1.6))
            text_color = _PREFERRED_HOVER
            bold = False
        else:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(pal["card_border"]), 1.2))
            text_color = QColor(pal["ink"])
            bold = False
        p.drawRoundedRect(rect, r, r)
        f = QFont(self.font())
        f.setBold(bold)
        p.setFont(f)
        p.setPen(text_color)
        p.drawText(rect, int(Qt.AlignmentFlag.AlignCenter), self._label())
        p.end()


# ── StylePicker ──────────────────────────────────────────────────────

#: Same genre set as the Editor's Style combo
#: (mira/ui/edited/adjustment_surface.py::_STYLES). The Style column on
#: ``item`` accepts any string, but limiting the picker to the calibrated
#: list keeps Edit-phase auto-correction routing predictable.
STYLES: tuple[str, ...] = (
    "general", "portrait", "macro", "wildlife",
    "landscape", "selfie", "night_long_exposure",
)


def _style_label(key: str) -> str:
    return key.replace("_", " ").title()


class StylePicker(QComboBox):
    """A small genre picker reused on the review chrome row.

    Per spec/159 §5.3, Style is per-source-item — editing it propagates
    across every shipped version. The picker emits :data:`style_picked`
    only on USER input (it suppresses signals when the host pushes the
    canonical value back in via :meth:`setStyle`).

    Inherits :class:`QComboBox` so the existing ``#ProcessStyleCombo``
    QSS role (assets/themes/redesign.qss) styles it without any inline
    setStyleSheet.
    """

    #: Emits the chosen style key (``'portrait'`` etc.); never the
    #: human label.
    style_picked = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProcessStyleCombo")
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        for key in STYLES:
            self.addItem(_style_label(key), key)
        self.setToolTip(tr(
            "Style — what genre this shot is (portrait, landscape, "
            "macro, …). Saved on the source item so every shipped "
            "version reads the same Style."))
        # ``activated`` fires on every USER pick (including re-picking
        # the shown value) and never programmatically — mirrors the
        # Editor's classification capture path (spec/58 §2).
        self.activated.connect(self._on_activated)

    def style(self) -> str:
        data = self.currentData()
        return data if isinstance(data, str) else "general"

    def setStyle(self, key: Optional[str]) -> None:  # noqa: N802 — Qt-like
        """Push a value from the host without emitting :data:`style_picked`."""
        if not key:
            key = "general"
        idx = self.findData(key)
        if idx < 0:
            idx = self.findData("general")
        was_blocked = self.blockSignals(True)
        try:
            self.setCurrentIndex(max(0, idx))
        finally:
            self.blockSignals(was_blocked)

    def _on_activated(self, _index: int) -> None:
        data = self.currentData()
        if isinstance(data, str):
            self.style_picked.emit(data)


__all__ = [
    "COLOR_LABEL_HEX",
    "COLOR_LABEL_ORDER",
    "STYLES",
    "StarRow",
    "ColorLabelRow",
    "ColorLabelMultiRow",
    "FlagToggle",
    "DeleteToggle",
    "PreferredToggle",
    "StylePicker",
]
