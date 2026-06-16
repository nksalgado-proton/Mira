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
from mira.ui.palette import PALETTE, RADIUS


TILE_RADIUS = float(RADIUS["xl"])    # spec/77 §10.7 — tiles are rounded


_CATEGORY_ICONS_DIR = (
    Path(__file__).resolve().parents[3] / "assets" / "icons" / "categories"
)

# spec/77 §1 — the tile is a fixed title row on top of a 4:3 content
# area. The size-slider experiment (the prior §10.5 revision) was pulled
# in the 2026-06-16 follow-up: a fixed 248-px tile keeps the grid
# uniform and lets the title row + donut % type sit at one calibrated
# size, so we don't have to choose between "names truncate at 196" and
# "donuts swim in dead space at 400".
TILE_WIDTH = 248
TILE_PREFERRED_WIDTH = TILE_WIDTH   # legacy alias kept stable
TITLE_ROW_HEIGHT = 54
TILE_TOTAL_HEIGHT = TITLE_ROW_HEIGHT + int(TILE_WIDTH * 3 / 4)


_PHASES = ("collect", "pick", "edit", "export")


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
        # spec/77 §4 — TOP-anchor the ring + tight gap + % group. The
        # earlier "centre the group vertically" reading floated the
        # ``%`` toward the middle of its cell, which put the top-row
        # ``%`` closer to the bottom-row ring than to its own ring.
        # Now: ring rides the top of the cell, ``%`` sits ~4px under
        # it, all remaining vertical space falls BELOW the ``%`` (i.e.
        # before the next row in the 2×2). Same recipe in every cell
        # so the four read identically.
        pct_text = f"{self._percent}%"
        pct_font = self._pct_font()
        fm = QFontMetrics(pct_font)
        pct_h = fm.height()

        # The ring is the smaller of (width minus inset) and an upper
        # bound that leaves room for the gap + ``%`` line below it.
        avail_h = self.height() - pct_h - self._PCT_GAP - self._RING_INSET * 2
        side = min(
            self.width() - self._RING_INSET * 2, max(0, avail_h)
        )
        if side <= 0:
            return
        top = float(self._RING_INSET)
        rect = QRectF(
            (self.width() - side) / 2.0,
            top,
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
        # family via the HiDPI-aware ``tinted_svg_pixmap`` (renders at
        # ``size × DPR`` physical pixels, ``setDevicePixelRatio`` on the
        # result), so the line-icon stays sharp on 2× screens. We
        # position by the LOGICAL ``icon_size`` (not ``pm.width()``,
        # which returns the physical pixel count and would scale the
        # offset with the DPR).
        hole = side - ring_w * 2
        icon_size = max(16, int(hole * 0.58))
        if self._icon_path is not None and self._icon_path.exists():
            ink = QColor(_palette_color("ink", "#e4e8f5"))
            pm = tinted_svg_pixmap(self._icon_path, icon_size, ink)
            if not pm.isNull():
                ix = int(rect.center().x() - icon_size / 2)
                iy = int(rect.center().y() - icon_size / 2)
                painter.drawPixmap(ix, iy, pm)

        # Percent text — below the ring, centred horizontally. The
        # baseline is anchored against the bottom of the centred group
        # (spec/77 §10.7 #5) so every cell renders the same.
        painter.setFont(pct_font)
        painter.setPen(QColor(_palette_color("ink", "#e4e8f5")))
        text_w = fm.horizontalAdvance(pct_text)
        tx = (self.width() - text_w) / 2.0
        ty = top + side + self._PCT_GAP + fm.ascent()
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
    """Pick — default-Skip survival pass (spec/77 §10.7 #6).

    Default-Skip means every captured photo is implicitly skipped until
    the user picks it green; the ring therefore starts FULL RED and
    green grows out of it. No faint remainder — the ``decided`` count
    no longer matters for the ring; only ``picked`` does.

    * 0 captured  → full red (the event has photos coming, none kept).
    * captured>0  → green = picked / captured, red = remainder.
    * The ``decided`` parameter is accepted for API stability with the
      gateway aggregate but is not used in the slice math any more.
    """
    captured = max(0, int(captured))
    picked = max(0, min(captured, int(picked)))
    if captured == 0:
        return 0, [(1.0, "red")]
    percent = int(round(picked / captured * 100))
    return percent, [
        (picked, "green"),
        (captured - picked, "red"),
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
    """Export — default-Skip survival pass (spec/77 §10.7 #6).

    Mirrors Pick: every keeper is implicitly NOT-shipped until the user
    exports it, so the ring starts FULL RED and green grows out of it.
    No faint remainder.

    * 0 picked  → full red (no keepers yet, nothing to ship).
    * picked>0  → green = exported / picked, red = remainder.
    """
    picked = max(0, int(picked))
    exported = max(0, min(picked, int(exported)))
    if picked == 0:
        return 0, [(1.0, "red")]
    percent = int(round(exported / picked * 100))
    return percent, [
        (exported, "green"),
        (picked - exported, "red"),
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
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent, padded=False)
        # spec/77 §7.2 — let QSS draw the tile's rounded fill + visible
        # border by tagging as ``#TileCard`` and flipping
        # ``WA_StyledBackground`` so the rule actually paints (without
        # this, a QFrame doesn't render its QSS background reliably).
        # No paintEvent override — the half-built paint-the-border path
        # is the reason the v3 build shipped without a border at all.
        self.setObjectName("TileCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self._data = data
        self._sample_pixmaps = list(sample_pixmaps or [])
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_title_row())
        if data.is_closed:
            outer.addWidget(self._build_closed_content(), 1)
        else:
            outer.addWidget(self._build_open_content(), 1)
        self.setFixedSize(QSize(TILE_WIDTH, TILE_TOTAL_HEIGHT))
        self.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(TILE_WIDTH, TILE_TOTAL_HEIGHT)

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

        # The ⋮ is the only affordance in the title row now that the
        # status pill is gone (spec/77 §10.2 revised): flat, borderless,
        # 16-px glyph, hover-only background. Hit target ~22 px square
        # so the click is still reachable; the QSS ``#TileMore`` role
        # carries the look.
        more = QPushButton("⋮")
        more.setObjectName("TileMore")
        more.setFixedSize(22, 22)
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
        # spec/77 §3 — the closed photo is **unobstructed**. No
        # counts strip, no caption, no tag, no pill on top of the
        # image: the photo fills the 4:3 area and shines. The
        # exported/shot counts live elsewhere (event header, stats
        # surfaces) — they don't belong on the cover.
        #
        # The cycler sits below the title row so its top edge stays
        # square (meets the row at a straight line); the bottom corners
        # match the tile's outer radius (spec/77 §7.2) so the photo and
        # the tile border share one continuous rounded edge.
        return PhotoCycler(
            self._sample_pixmaps,
            caption="",
            sub_caption="",
            tag_text="",
            pill_text="",
            top_radius=0,
            bottom_radius=TILE_RADIUS,
        )

    # ── open content: 2×2 phase donut grid ───────────────────────

    def _build_open_content(self) -> QWidget:
        host = QWidget()
        grid = QGridLayout(host)
        # spec/77 §10.7 #2 — bigger bottom margin so the bottom-row
        # donut `%` labels never collide with the painted tile border
        # below them. Equal top/sides so the four cells stay uniform
        # (#5 — every donut reads identically).
        grid.setContentsMargins(10, 8, 10, 18)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)

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
