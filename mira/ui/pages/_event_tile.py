"""EventTile — Surface 01 fixed-height tile (spec/75 §5–§6).

Replaces the legacy tall ``EventCardRedesign`` for the redesigned events
screen: closed and open events both live in the same fixed-height box so
the grid reads as a uniform tile rail, ~3 columns × 3–4 rows visible.

Variants share the same outer ``Card`` chrome + the same fixed height,
so the grid stays uniform regardless of which mix of open/closed events
is on screen:

  Closed tile:  the ``PhotoCycler`` IS the tile body — chrome-free
                ambient slideshow of the event's exported keepers, with
                the event name + counts painted as a caption strip
                inside the cycler. The whole tile is a single click
                target that routes to Cuts.

  Open tile:    two-row layout. Top row carries the category icon, name
                (ellipsized) + Trip/Session tag, and an "● Open" pill
                pinned right. A sub-line below carries year · category ·
                Nd. The bottom row is the compact 4-phase pipeline strip
                (Collect / Pick / Edit / Export) pinned to the floor so
                every open tile lines its pipeline up the same vertical
                offset.

Per-phase colours follow the spec/66 identity: Collect blue · Pick
accent · Edit amber · Export green. ``done`` ≥ 100%; ``in-progress`` =
anything between 0 and 100; ``zero`` falls to the faint ``track`` colour.

Signals mirror the legacy contract:

    activated       click anywhere on the tile body
    title_clicked   the name (Event Header)
    info_clicked    legacy stub, kept for compatibility
    plan_requested  the day-count chip / opens the Days Table
    status_toggled  the Open/Closed pill
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.ui.base.event_card import EventCardData
from mira.ui.design import (
    Card,
    PhotoCycler,
    chip_closed,
    chip_open,
    tag,
    tinted_svg_pixmap,
)
from mira.ui.palette import PALETTE


_CATEGORY_ICONS_DIR = (
    Path(__file__).resolve().parents[3] / "assets" / "icons" / "categories"
)

# Spec/75 §4 — the closed photo tile drives this number. 150px is the
# floor where a landscape photo still reads contained over its blurred
# backdrop; the open tile inherits the same height so the grid is one
# uniform rail regardless of which mix of events is on screen.
TILE_HEIGHT = 150
# Spec/75 §4 — `minmax(210px, 1fr)` equivalent. FlowLayout packs by
# sizeHint so we give the tile a preferred width that lets ~3 columns fit
# the typical desktop window; the host's FlowLayout reflows on resize.
TILE_PREFERRED_WIDTH = 260
TILE_MIN_WIDTH = 220


_PHASES = ("collect", "pick", "edit", "export")
_PHASE_LABEL = {
    "collect": "Collect",
    "pick": "Pick",
    "edit": "Edit",
    "export": "Export",
}
_PHASE_COLOR_TOKEN = {
    "collect": "blue",
    "pick": "accent",
    "edit": "amber",
    "export": "green",
}


def _palette_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


def _palette_color(token: str) -> str:
    return PALETTE[_palette_mode()].get(token, "#7c6cff")


def _stage_percent(status_map: dict | None) -> int:
    """Aggregate per-day status into a 0..100 percent for one phase.

    ``status_map`` looks like {day_number: STATUS_DONE | STATUS_IN_PROGRESS
    | STATUS_NOT_STARTED}. A half-credit is given to in-progress days so a
    long-running phase shows movement before any day reaches done.
    """
    if not status_map:
        return 0
    total = len(status_map)
    if total == 0:
        return 0
    done = sum(1 for s in status_map.values() if s == "done")
    in_prog = sum(1 for s in status_map.values() if s == "in_progress")
    percent = int(round((done + 0.5 * in_prog) / total * 100))
    return max(0, min(100, percent))


def _year_str(d: Optional[date]) -> str:
    return str(d.year) if d else ""


# ── Category icon tile — small line-icon over a card2 backdrop ──────


class _CategoryIcon(QFrame):
    """28×28 backing tile for the category line-icon."""

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
    _TILE_SIZE = 28
    _ICON_SIZE = 18

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
        # Inline style: the tile is a tiny chrome bit that paints itself.
        # No themable QSS role covers "small card2 holder with no
        # border"; matches the pattern in _event_card_redesign.py.
        self.setStyleSheet(
            "background: #1e222d; border: none; border-radius: 8px;"
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


# ── Compact 4-phase pipeline strip — pinned to the open tile floor ──


class _PipelineStrip(QWidget):
    """Painted 4-segment phase pipeline.

    One row of equal segments (Collect / Pick / Edit / Export) with a
    coloured fill per segment showing that phase's progress. The fill
    colour is the phase identity colour (Collect blue · Pick accent ·
    Edit amber · Export green); the *length* of the fill encodes
    progress. Zero-progress segments draw the faint track colour. Tiny
    per-phase percentage tags float above each segment so the strip
    reads as both a pipeline AND a status row.

    Painted (not assembled from child widgets) so the strip is one flat
    box — fewer Qt widgets per tile = the grid stays cheap at 9–12 tiles
    visible.
    """

    _SEG_HEIGHT = 8
    _SEG_RADIUS = 4
    _SEG_GAP = 6
    _LABEL_ROW = 14   # px reserved above the bar for the small labels

    def __init__(
        self,
        percents: list[int],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._percents = list(percents)
        self.setFixedHeight(self._SEG_HEIGHT + self._LABEL_ROW + 2)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )

    def paintEvent(self, _evt) -> None:  # noqa: N802 — Qt override
        if not self._percents:
            return
        pal = PALETTE[_palette_mode()]
        track = QColor(pal.get("track", "#262b38"))
        ink_soft = QColor(pal.get("ink_soft", "#8b94a7"))

        n = len(self._percents)
        gap = self._SEG_GAP
        avail = self.width() - gap * (n - 1)
        seg_w = max(1, avail // n)

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)

        y_bar = self._LABEL_ROW + 2
        for i, percent in enumerate(self._percents):
            phase = _PHASES[i] if i < len(_PHASES) else _PHASES[-1]
            x = i * (seg_w + gap)
            # Track
            p.setBrush(track)
            p.drawRoundedRect(
                x, y_bar, seg_w, self._SEG_HEIGHT,
                self._SEG_RADIUS, self._SEG_RADIUS,
            )
            # Fill — phase identity colour, length = progress.
            if percent > 0:
                fill_color = QColor(
                    pal.get(_PHASE_COLOR_TOKEN[phase], pal.get("accent"))
                )
                fill_w = max(1, int(seg_w * percent / 100.0))
                p.setBrush(fill_color)
                p.drawRoundedRect(
                    x, y_bar, fill_w, self._SEG_HEIGHT,
                    self._SEG_RADIUS, self._SEG_RADIUS,
                )
            # Tiny micro-cap label above the segment — phase name + %.
            label = f"{_PHASE_LABEL[phase][:4]} {percent}%"
            p.setPen(ink_soft)
            f = self.font()
            f.setPointSizeF(max(7.5, f.pointSizeF() - 2.5))
            p.setFont(f)
            p.drawText(
                x, 0, seg_w, self._LABEL_ROW,
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                label,
            )
            p.setPen(Qt.PenStyle.NoPen)
        p.end()


# ── The tile ────────────────────────────────────────────────────────


class EventTile(Card):
    """Uniform fixed-height event tile — closed (PhotoCycler) or open
    (compact 4-phase pipeline). See module docstring for the signal
    contract."""

    activated = pyqtSignal(str)
    title_clicked = pyqtSignal(str)
    info_clicked = pyqtSignal(str)
    plan_requested = pyqtSignal(str)
    status_toggled = pyqtSignal(str)

    def __init__(
        self,
        data: EventCardData,
        *,
        sample_pixmaps: Optional[List[QPixmap]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent, padded=False)
        self._data = data
        self._sample_pixmaps = list(sample_pixmaps or [])
        # Fixed box: the grid wants every tile identical so the rail
        # reads as a uniform field of tiles. Width has a sensible
        # preferred so FlowLayout packs ~3 per row at the typical
        # desktop width; the FlowLayout reflows on resize.
        self.setFixedHeight(TILE_HEIGHT)
        self.setMinimumWidth(TILE_MIN_WIDTH)
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed
        )
        # Whole-tile click targets activate the event. Children with
        # their own handlers (title, status pill, day chip) catch their
        # clicks first via Qt's child-first flow.
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        if data.is_closed:
            self._build_closed_body()
        else:
            self._build_open_body()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(TILE_PREFERRED_WIDTH, TILE_HEIGHT)

    # ── closed: PhotoCycler IS the tile ─────────────────────────────

    def _build_closed_body(self) -> None:
        sub_bits: list[str] = []
        if self._data.exported_count:
            sub_bits.append(f"{self._data.exported_count} exported")
        if self._data.collected_count and self._data.collected_count != \
                self._data.exported_count:
            sub_bits.append(f"{self._data.collected_count} shot")
        if not sub_bits and self._data.total_days:
            sub_bits.append(f"{self._data.total_days} days")
        tag_text = ""
        et = (self._data.event_type or "").lower()
        if et in ("trip", "session"):
            tag_text = et.upper()
        cycler = PhotoCycler(
            self._sample_pixmaps,
            caption=self._data.name or "(untitled)",
            sub_caption=" · ".join(sub_bits),
            tag_text=tag_text,
            pill_text="Closed",
        )
        cycler.setCursor(Qt.CursorShape.PointingHandCursor)
        self._cycler = cycler
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(cycler)

    # ── open: top row + sub + pinned pipeline ───────────────────────

    def _build_open_body(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 10)
        outer.setSpacing(4)

        # Top row — icon · name + tag · status pill pinned right.
        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(_CategoryIcon(
            self._data.event_type or "unclassified",
            self._data.event_subtype,
        ))
        title_box = QHBoxLayout()
        title_box.setSpacing(6)
        title = QLabel(self._data.name or "(untitled)")
        title.setObjectName("CardTitle")
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        title.setCursor(Qt.CursorShape.PointingHandCursor)
        title.mousePressEvent = (
            lambda _evt: self.title_clicked.emit(self._data.event_id)
        )
        # Ellipsize via the label's elide setting through a fixed
        # sizePolicy: let it shrink horizontally so the status pill stays
        # pinned right without pushing off-screen on narrow tiles.
        title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        title_box.addWidget(title, 1)
        et_tag = self._event_type_tag()
        if et_tag is not None:
            title_box.addWidget(et_tag, 0)
        top.addLayout(title_box, 1)
        status_pill = chip_open("● Open")
        status_pill.setCursor(Qt.CursorShape.PointingHandCursor)
        status_pill.mousePressEvent = (
            lambda _evt: self.status_toggled.emit(self._data.event_id)
        )
        top.addWidget(status_pill, 0, Qt.AlignmentFlag.AlignRight)
        outer.addLayout(top)

        # Sub line — year · category · Nd. Clicking the day-count
        # opens the Days Table (legacy contract). We keep the line as
        # one label and overlay a tooltip on the whole row.
        sub = self._compose_subline()
        sub_label = QLabel(sub)
        sub_label.setObjectName("Sub")
        sub_label.setStyleSheet("font-size: 11px;")
        if self._data.total_days:
            sub_label.setCursor(Qt.CursorShape.PointingHandCursor)
            sub_label.setToolTip("Open the event's Days Table")
            sub_label.mousePressEvent = (
                lambda _evt: self.plan_requested.emit(self._data.event_id)
            )
        outer.addWidget(sub_label)

        outer.addStretch(1)

        # Bottom row pinned — compact pipeline strip.
        percents = [
            _stage_percent(self._data.status_by_phase.get(p, {})
                           if self._data.status_by_phase else None)
            for p in _PHASES
        ]
        self._pipeline = _PipelineStrip(percents)
        outer.addWidget(self._pipeline)

    def _event_type_tag(self) -> Optional[QLabel]:
        et = (self._data.event_type or "").lower()
        if et in ("trip", "session"):
            return tag(et.upper())
        return None

    def _compose_subline(self) -> str:
        bits: list[str] = []
        year = _year_str(self._data.start_date)
        if year:
            bits.append(year)
        et = (self._data.event_type or "").strip()
        if et and et.lower() != "unclassified":
            bits.append(et.title())
        if self._data.event_subtype:
            bits.append(self._data.event_subtype)
        if self._data.total_days:
            bits.append(f"{self._data.total_days}d")
        return " · ".join(bits)

    # ── click → activate ────────────────────────────────────────────

    def mousePressEvent(self, evt) -> None:  # noqa: N802 — Qt override
        """The tile is one click target. Children with their own handlers
        (title, status pill, day-count sub-line) catch their clicks first
        via Qt's child-first flow."""
        super().mousePressEvent(evt)
        self.activated.emit(self._data.event_id)
