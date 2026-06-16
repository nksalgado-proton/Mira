"""EventTile v2 — Surface 01 tile (spec/77).

Supersedes the spec/75 fixed-150-px box. The tile is now a fixed title row
on top of a 4:3 **content area** (not a 4:3 whole tile — the 4:3 applies
to the content below the title). Two variants share the title row:

  Open tile:    title row + 2×2 grid of phase donuts
                (Collect top-left, Pick top-right, Edit bottom-left,
                Export bottom-right). Each donut shows the phase icon
                centred + the percentage just below it. Donut semantics
                per spec/77 §4 — Collect/Edit are amber→green
                progress, Pick/Export are green/red survival passes.

  Closed tile:  title row + the chrome-free ``PhotoCycler`` (spec/75 §6)
                inside the 4:3 area, with a single thin translucent
                counts strip ("N exported · M shot") painted across the
                bottom of the photo so nothing else covers the image.

The title row carries: a category icon tile · a name + meta block · a
green/pink status pill · a ``⋮`` menu button. The name sits on its own
near-full-width line so it stops truncating in the common case (the
Picture-21 bug from spec/75's row layout). The ``⋮`` menu carries the
rare actions (Close / Reopen / Header / Days table / Delete) so the tile
stays clean.

Signals — same legacy contract the spec/75 tile shipped, plus two:

    activated         click anywhere on the body
    title_clicked     the name (Event Header)
    info_clicked      legacy stub, kept for compatibility
    plan_requested    the meta line / Days Table action
    status_toggled    the pill click (toggles open/closed)
    delete_requested  the menu Delete entry
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QPointF, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.ui.base.event_card import EventCardData
from mira.ui.design import (
    Card,
    PHASE_GLYPH,
    PhotoCycler,
    tinted_svg_pixmap,
)
from mira.ui.palette import PALETTE


_CATEGORY_ICONS_DIR = (
    Path(__file__).resolve().parents[3] / "assets" / "icons" / "categories"
)

# spec/77 §1 + §10.5 — the tile is a fixed title row on top of a 4:3
# content area; the slider in the events toolbar scales the tile width
# live. Header text stays the same size at every width (§10.5), so the
# title row height is a constant — only the 4:3 area + donuts grow.
TILE_DEFAULT_WIDTH = 248       # the approved-mock comfortable size
TILE_MIN_WIDTH = 196           # narrow enough the slider is useful…
TILE_MAX_WIDTH = 400           # …without shrinking past donut % legibility
TILE_PREFERRED_WIDTH = TILE_DEFAULT_WIDTH    # legacy alias kept stable
TITLE_ROW_HEIGHT = 54


_PHASES = ("collect", "pick", "edit", "export")


def total_tile_height(tile_width: int) -> int:
    """The full tile height for a given tile width — the title row is a
    constant; the content area is a 4:3 box below it."""
    return TITLE_ROW_HEIGHT + int(tile_width * 3 / 4)


def _palette_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


def _palette_color(token: str, fallback: str = "#7c6cff") -> str:
    return PALETTE[_palette_mode()].get(token, fallback)


def _year_str(d: Optional[date]) -> str:
    return str(d.year) if d else ""


# ── Category icon tile (header) ─────────────────────────────────────


class _CategoryIcon(QFrame):
    """32×32 backing tile holding the event's category line-icon."""

    _SUBTYPE_TO_ICON = {
        "wildlife": "wildlife",
        "birds": "birds",
        "bird": "birds",
        "mountains": "mountains",
        "mountain": "mountains",
        "road": "road",
        "tourism": "tourism",
        "adventure": "adventure",
        "landscape": "landscape",
        "urban": "urban",
        "street": "urban",
        "macro": "macro",
        "inseto": "macro",
        "insetos": "macro",
        "portrait": "wildlife",
        "candid": "wildlife",
        "details": "macro",
        "mammals": "wildlife",
        "reptiles": "wildlife",
    }
    _TYPE_DEFAULT = {"trip": "tourism", "session": "macro"}
    _TILE_SIZE = 32
    _ICON_SIZE = 20

    def __init__(
        self,
        event_type: str,
        event_subtype: Optional[str] = None,
        *,
        dim: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(self._TILE_SIZE, self._TILE_SIZE)
        sub_key = (event_subtype or "").strip().lower()
        type_key = (event_type or "").strip().lower()
        icon_name = (
            self._SUBTYPE_TO_ICON.get(sub_key)
            or self._TYPE_DEFAULT.get(type_key)
            or "tourism"
        )
        path = _CATEGORY_ICONS_DIR / f"{icon_name}.svg"
        self._icon_path = path if path.exists() else None
        self._tint = QColor("#8b94a7" if dim else "#7c6cff")
        # Inline style: the tile is a tiny self-painted chrome bit; no
        # themable QSS role covers "small card2 holder with no border".
        self.setStyleSheet(
            "background: #1e222d; border: none; border-radius: 9px;"
        )

    def paintEvent(self, evt) -> None:  # noqa: N802 — Qt override
        super().paintEvent(evt)
        if self._icon_path is None:
            return
        pm = tinted_svg_pixmap(self._icon_path, self._ICON_SIZE, self._tint)
        if pm.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        x = (self.width() - self._ICON_SIZE) // 2
        y = (self.height() - self._ICON_SIZE) // 2
        p.drawPixmap(x, y, pm)
        p.end()


# ── Phase donut (open-tile 2×2 grid cell) ───────────────────────────


class _PhaseDonut(QWidget):
    """One phase's donut, with the **phase icon centred inside the ring
    (only)** and the **percentage rendered just below the ring** —
    spec/77 §10.4 supersedes the prior icon+% stacked-in-centre layout
    (it made both feel small and low-res). Painted as one widget so a
    grid of 4 cells stays light; the crisp SVG phase glyphs come from
    ``PHASE_GLYPH`` (the same family the rest of the app uses).

    ``slices`` is a list of (value, color_token) tuples — proportional
    weights filling one full ring; colors resolve from the live palette
    so theme toggles re-paint without a rebuild. Use
    ``("track", remaining)`` for the faint remainder slice.
    """

    _RING_THICKNESS_RATIO = 0.11
    _RING_INSET = 3
    _PCT_GAP = 4

    def __init__(
        self,
        phase: str,
        percent: int,
        slices: list[tuple[float, str]],
        *,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._phase = phase
        self._percent = max(0, min(100, int(percent)))
        self._slices = list(slices)
        self._icon_path = PHASE_GLYPH.get(phase)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        # Ring + a one-line % label below it. 78 px is the floor where
        # the smallest scale-down still keeps the % legible.
        self.setMinimumSize(QSize(78, 92))

    def _pct_font(self) -> QFont:
        f = QFont(self.font())
        f.setPixelSize(12)
        f.setWeight(QFont.Weight.Bold)
        return f

    def paintEvent(self, _evt) -> None:  # noqa: N802 — Qt override
        # Reserve the bottom strip for the % label; the ring fills the
        # rest, centred horizontally so the four donut cells line up.
        pct_text = f"{self._percent}%"
        pct_font = self._pct_font()
        fm = QFontMetrics(pct_font)
        pct_h = fm.height()

        ring_area_h = self.height() - pct_h - self._PCT_GAP
        side = min(self.width(), ring_area_h) - self._RING_INSET * 2
        if side <= 0:
            return
        rect = QRectF(
            (self.width() - side) / 2,
            (ring_area_h - side) / 2,
            side, side,
        )
        ring_w = max(6, int(side * self._RING_THICKNESS_RATIO))

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        inset = ring_w / 2
        arc_rect = rect.adjusted(inset, inset, -inset, -inset)

        # Track ring underneath every slice so a zero-arc phase still
        # reads as a faint hoop (not as nothing at all).
        track_color = QColor(_palette_color("track", "#262b38"))
        pen = QPen(track_color, ring_w)
        pen.setCapStyle(Qt.PenCapStyle.FlatCap)
        painter.setPen(pen)
        painter.drawArc(arc_rect, 0, 360 * 16)

        # Slices — clockwise from 12 o'clock, FlatCap so adjacent arcs
        # butt cleanly. "track" slices skip drawing (the underlying
        # track already paints) but still advance the cursor so the
        # faint remainder occupies its true share of the ring.
        total = sum(max(0.0, v) for v, _ in self._slices) or 1.0
        start_angle = 90 * 16
        for value, token in self._slices:
            if value <= 0:
                continue
            span = -int(360 * 16 * (value / total))
            if token != "track":
                pen = QPen(QColor(_palette_color(token)), ring_w)
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                painter.setPen(pen)
                painter.drawArc(arc_rect, start_angle, span)
            start_angle += span

        # Phase icon — centred inside the ring's hole at ~58 % of the
        # hole's diameter. Drawn from the crisp ``PHASE_GLYPH`` SVG
        # family via ``tinted_svg_pixmap`` (cached per (path, size,
        # color)), so the line-icon stays sharp at any scale.
        hole = side - ring_w * 2
        icon_size = max(16, int(hole * 0.58))
        if self._icon_path is not None and self._icon_path.exists():
            ink = QColor(_palette_color("ink", "#e4e8f5"))
            pm = tinted_svg_pixmap(self._icon_path, icon_size, ink)
            if not pm.isNull():
                ix = int(rect.center().x() - pm.width() / 2)
                iy = int(rect.center().y() - pm.height() / 2)
                painter.drawPixmap(ix, iy, pm)

        # Percent text — below the ring, centred horizontally. The font
        # size is fixed so labels stay legible at every slider step
        # (spec/77 §10.5 — header text constant as the tile scales).
        painter.setFont(pct_font)
        painter.setPen(QColor(_palette_color("ink", "#e4e8f5")))
        text_w = fm.horizontalAdvance(pct_text)
        tx = (self.width() - text_w) / 2
        ty = ring_area_h + self._PCT_GAP + fm.ascent()
        painter.drawText(QPointF(tx, ty), pct_text)
        painter.end()


# ── Donut input — spec/77 §4 semantics ──────────────────────────────


def _collect_slices(
    days_with_captures: int, total_days: int
) -> tuple[int, list[tuple[float, str]]]:
    """Collect — amber → green progress.

    Numerator: days the user has captured anything on. Denominator: the
    total day count of the event (from the header span; spec/77 §5).
    Returns (percent, slices). 100% paints all green; partial paints
    amber+track; 0% paints just the track."""
    total = max(0, int(total_days))
    done = max(0, min(total, int(days_with_captures)))
    if total == 0:
        return 0, [(1.0, "track")]
    percent = int(round(done / total * 100))
    color = "green" if percent >= 100 else "amber"
    return percent, [(done, color), (total - done, "track")]


def _pick_slices(
    picked: int, decided: int, captured: int,
) -> tuple[int, list[tuple[float, str]]]:
    """Pick — green / red survival pass.

    Green = picked ÷ captured; red = skipped ÷ captured; faint =
    not-yet-reviewed. Centre % = picked share. Defaults to all-track at
    0 captured so a fresh event reads as a quiet ring."""
    captured = max(0, int(captured))
    decided = max(0, min(captured, int(decided)))
    picked = max(0, min(decided, int(picked)))
    skipped = max(0, decided - picked)
    if captured == 0:
        return 0, [(1.0, "track")]
    percent = int(round(picked / captured * 100))
    return percent, [
        (picked, "green"),
        (skipped, "red"),
        (captured - decided, "track"),
    ]


def _edit_slices(
    developed: int, picked: int
) -> tuple[int, list[tuple[float, str]]]:
    """Edit — amber → green progress. Numerator: keepers with a real
    user adjustment row; denominator: picked. Zero-picked falls to a
    faint track so the cell still reads as "nothing here yet" instead
    of "100%"."""
    picked = max(0, int(picked))
    developed = max(0, min(picked, int(developed)))
    if picked == 0:
        return 0, [(1.0, "track")]
    percent = int(round(developed / picked * 100))
    color = "green" if percent >= 100 else "amber"
    return percent, [(developed, color), (picked - developed, "track")]


def _export_slices(
    exported: int, picked: int
) -> tuple[int, list[tuple[float, str]]]:
    """Export — green + faint. Spec/77 §7 #2: the schema has no
    explicit drop decision (just ``edit_exported = 1`` for shipped), so
    the red arc the spec talks about is not yet wired — Export reads
    green (shipped) + faint (not yet shipped). When a deliberate-drop
    signal lands, plug the red slice in here and the donut updates
    everywhere at once."""
    picked = max(0, int(picked))
    exported = max(0, min(picked, int(exported)))
    if picked == 0:
        return 0, [(1.0, "track")]
    percent = int(round(exported / picked * 100))
    return percent, [
        (exported, "green"),
        (picked - exported, "track"),
    ]


# ── The tile ────────────────────────────────────────────────────────


class EventTile(Card):
    """Surface 01 fixed-shape event tile (spec/77). One title row on top
    of a 4:3 content area; the content varies (donut grid vs. cycler)
    but the outer shape is identical for every event."""

    activated = pyqtSignal(str)
    title_clicked = pyqtSignal(str)
    info_clicked = pyqtSignal(str)
    plan_requested = pyqtSignal(str)
    status_toggled = pyqtSignal(str)
    delete_requested = pyqtSignal(str)

    def __init__(
        self,
        data: EventCardData,
        *,
        sample_pixmaps: Optional[List[QPixmap]] = None,
        tile_width: int = TILE_DEFAULT_WIDTH,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent, padded=False)
        # spec/77 §10.3 — re-tag as #TileCard so the stronger border
        # role lands. Card's drop-shadow effect is already set up.
        self.setObjectName("TileCard")
        self._data = data
        self._sample_pixmaps = list(sample_pixmaps or [])
        # spec/77 §10.5 — the toolbar slider hands a width down to each
        # tile. The 4:3 content area scales with this; the title row's
        # height stays constant (header text size is constant by spec).
        self._tile_width = max(
            TILE_MIN_WIDTH, min(TILE_MAX_WIDTH, int(tile_width))
        )
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_title_row())
        if data.is_closed:
            outer.addWidget(self._build_closed_content(), 1)
        else:
            outer.addWidget(self._build_open_content(), 1)
        self.setFixedSize(QSize(
            self._tile_width, total_tile_height(self._tile_width)
        ))
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(
            self._tile_width, total_tile_height(self._tile_width)
        )

    # ── title row ─────────────────────────────────────────────────

    def _build_title_row(self) -> QWidget:
        host = QWidget()
        host.setFixedHeight(TITLE_ROW_HEIGHT)
        h = QHBoxLayout(host)
        h.setContentsMargins(12, 8, 8, 8)
        h.setSpacing(8)

        h.addWidget(_CategoryIcon(
            self._data.event_type or "unclassified",
            self._data.event_subtype,
            dim=self._data.is_closed,
        ), 0, Qt.AlignmentFlag.AlignVCenter)

        # Name + meta lockup. Spec/77 §10.1 retired the status pill —
        # the tile's body already says it (donuts = open, photo =
        # closed) — and that move gives the name the full remaining
        # header width, killing the truncation Nelson flagged on
        # Picture 21/22/23.
        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        title = QLabel(self._data.name or "(untitled)")
        title.setObjectName("TileTitle")
        title.setCursor(Qt.CursorShape.PointingHandCursor)
        title.mousePressEvent = (
            lambda _evt: self.title_clicked.emit(self._data.event_id)
        )
        # Ignored horizontal sizePolicy = Qt elides only when the actual
        # available width drops below the natural string width — so the
        # name gets *all* the remaining header width before truncating.
        title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        text_col.addWidget(title)
        meta = QLabel(self._compose_meta())
        meta.setObjectName("TileMeta")
        meta.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        if self._data.total_days:
            meta.setCursor(Qt.CursorShape.PointingHandCursor)
            meta.setToolTip("Open the event's Days Table")
            meta.mousePressEvent = (
                lambda _evt: self.plan_requested.emit(self._data.event_id)
            )
        text_col.addWidget(meta)
        h.addLayout(text_col, 1)

        # The ⋮ is now the only affordance in the title row (spec/77
        # §10.2): a solid, clearly-visible top-right control with a
        # standing background so it reads on any tile body — including
        # a closed tile, where the title row sits directly above the
        # photo. Styled via the `#TileMore` QSS role so light + dark
        # share one rule.
        more = QPushButton("⋮")
        more.setObjectName("TileMore")
        more.setFixedSize(28, 28)
        more.setCursor(Qt.CursorShape.PointingHandCursor)
        more.setToolTip("More actions")
        more.clicked.connect(self._open_more_menu)
        self._more_btn = more
        h.addWidget(more, 0, Qt.AlignmentFlag.AlignVCenter)
        return host

    def _compose_meta(self) -> str:
        bits: list[str] = []
        et = (self._data.event_type or "").strip()
        if et and et.lower() != "unclassified":
            bits.append(et.title())
        year = _year_str(self._data.start_date)
        if year:
            bits.append(year)
        if self._data.event_subtype:
            bits.append(self._data.event_subtype)
        if self._data.total_days:
            bits.append(f"{self._data.total_days}d")
        return " · ".join(bits) or " "

    def _open_more_menu(self) -> None:
        """⋮ menu — Close/Reopen + Header + Days table + Delete.

        Spec/77 §6: Reopen must always work on a closed tile, regardless
        of whether the event has exports. The current handlers route
        through ``status_toggled`` (which the host wires to the same
        Close/Reopen toggle the menu action used to drive), so the
        Reopen menu entry is just a labelled alias for that signal."""
        menu = QMenu(self)
        if self._data.is_closed:
            reopen = menu.addAction("Reopen event")
            reopen.triggered.connect(
                lambda: self.status_toggled.emit(self._data.event_id)
            )
        else:
            close = menu.addAction("Close event")
            close.triggered.connect(
                lambda: self.status_toggled.emit(self._data.event_id)
            )
        header = menu.addAction("Event header…")
        header.triggered.connect(
            lambda: self.title_clicked.emit(self._data.event_id)
        )
        if not self._data.is_closed:
            days = menu.addAction("Days table…")
            days.triggered.connect(
                lambda: self.plan_requested.emit(self._data.event_id)
            )
        menu.addSeparator()
        delete = menu.addAction("Delete…")
        delete.triggered.connect(
            lambda: self.delete_requested.emit(self._data.event_id)
        )
        menu.exec(self._more_btn.mapToGlobal(
            self._more_btn.rect().bottomLeft()
        ))

    # ── closed content: PhotoCycler in the 4:3 area ──────────────

    def _build_closed_content(self) -> QWidget:
        sub_bits: list[str] = []
        if self._data.exported_count:
            sub_bits.append(f"{self._data.exported_count} exported")
        if self._data.collected_count:
            sub_bits.append(f"{self._data.collected_count} shot")
        return PhotoCycler(
            self._sample_pixmaps,
            caption=" · ".join(sub_bits),
            sub_caption="",
            tag_text="",
            pill_text="",
        )

    # ── open content: 2×2 phase donut grid ───────────────────────

    def _build_open_content(self) -> QWidget:
        host = QWidget()
        grid = QGridLayout(host)
        grid.setContentsMargins(8, 8, 8, 8)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

        pct, slices = _collect_slices(
            self._data.days_with_captures, self._data.total_days,
        )
        grid.addWidget(_PhaseDonut("collect", pct, slices), 0, 0)

        pct, slices = _pick_slices(
            self._data.picked_count,
            self._data.decided_count,
            self._data.collected_count,
        )
        grid.addWidget(_PhaseDonut("pick", pct, slices), 0, 1)

        pct, slices = _edit_slices(
            self._data.developed_count, self._data.picked_count,
        )
        grid.addWidget(_PhaseDonut("edit", pct, slices), 1, 0)

        pct, slices = _export_slices(
            self._data.exported_count, self._data.picked_count,
        )
        grid.addWidget(_PhaseDonut("export", pct, slices), 1, 1)
        return host

    # ── click → activate ────────────────────────────────────────

    def mousePressEvent(self, evt) -> None:  # noqa: N802 — Qt override
        """The tile is one click target. Children with their own
        handlers (title, status pill, ⋮ button, meta-line plan request)
        catch their clicks first via Qt's child-first event flow."""
        super().mousePressEvent(evt)
        self.activated.emit(self._data.event_id)
