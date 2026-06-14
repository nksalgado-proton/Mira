"""EventCardRedesign — the Surface 01 event tile.

Two variants share one header strip + diverge below:

    Open card:
        Header:  category icon tile · title + Trip|Session tag · sub
                 (year · category) · StatusPill(Open, green) · day chip
        Body:    pipeline progress — 4 StageProgress bars labeled
                 Collect / Pick / Edit / Share, sourced from
                 EventCardData.status_by_phase.

    Closed card:
        Header:  same shape but StatusPill is Closed (pink) and the icon
                 tile uses a muted backdrop.
        Body:    Carousel of exported sample photos + StatTile grid
                 (Collected / Picked / Edited / Exported with count +
                 percentage) + classification tag chip strip.

Signals mirror the legacy DashboardPage contract so the host (MainWindow)
re-uses the same routing without changes:

    activated         -> event_activated     (body click -> dashboard / cuts)
    title_clicked     -> event_info_requested (Event Header dialog)
    info_clicked      -> event_plan_requested (Days Table dialog)
    status_toggled    -> event_status_toggle_requested (Open <-> Closed)

The data comes from the existing :class:`mira.ui.base.event_card.EventCardData`
shape; the data layer is unchanged. Only the visuals are redesigned.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.ui.base.event_card import EventCardData
from mira.ui.design import (
    Card,
    Carousel,
    PHASE_GLYPH,
    StageProgress,
    StatTile,
    chip_closed,
    chip_idle,
    chip_open,
    tag,
    tinted_svg_pixmap,
)
from mira.ui.palette import PALETTE


_CATEGORY_ICONS_DIR = (
    Path(__file__).resolve().parents[3]
    / "assets" / "icons" / "categories"
)


_PHASES = ("collect", "pick", "edit", "export")
_PHASE_LABEL = {
    "collect": "Collect",
    "pick": "Pick",
    "edit": "Edit",
    "export": "Export",
}
# Per-phase identity colours (spec/66) — same language as the closed-card stat
# tiles: Collect blue · Pick accent · Edit amber · Export green. PALETTE tokens
# so the bars follow theme toggles.
_PHASE_COLOR_TOKEN = {
    "collect": "blue",
    "pick": "accent",
    "edit": "amber",
    "export": "green",
}
_PHASE_PCT_ROLE = {
    "collect": "PctCollect",
    "pick": "PctPick",
    "edit": "PctEdit",
    "export": "PctExport",
}


def _stage_value(status_map: dict[int, str] | None) -> tuple[int, str | None]:
    """Aggregate per-day status into (percent, state) for one phase.

    Status_map looks like {day_number: STATUS_DONE | STATUS_IN_PROGRESS |
    STATUS_NOT_STARTED}. Percent = decided/total*100; state mirrors
    StageProgress's state vocabulary so the bar paints in the right color.
    """
    if not status_map:
        return 0, None
    total = len(status_map)
    if total == 0:
        return 0, None
    done = sum(1 for s in status_map.values() if s == "done")
    in_prog = sum(1 for s in status_map.values() if s == "in_progress")
    if done == total:
        return 100, "done"
    if done + in_prog == 0:
        return 0, None
    percent = int(round((done + 0.5 * in_prog) / total * 100))
    return max(0, min(100, percent)), "prog"


def _year_str(d: date | None) -> str:
    return str(d.year) if d else ""


class _CategoryTile(QFrame):
    """46px square card2-backed tile holding the event's category icon.

    Uses real SVG icons from ``assets/icons/categories/`` tinted in code.
    Subtype lookup wins; falls back to a type-level default. Open events
    get the accent tint on a card2 backdrop with no border — quiet, so the
    tile recedes into the card (mockup spec, surface-01-initial-app.html).
    Closed events drop further to ink_soft tint.
    """

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
        "inseto": "macro",         # bug / insect events fit the macro family
        "insetos": "macro",
        "portrait": "wildlife",    # closest line-icon stand-in until we add one
        "candid": "wildlife",
        "details": "macro",
        "mammals": "wildlife",
        "reptiles": "wildlife",
    }
    _TYPE_DEFAULT = {
        "trip": "tourism",
        "session": "macro",
    }

    def __init__(
        self,
        event_type: str,
        event_subtype: str | None = None,
        *,
        dim: bool = False,
    ) -> None:
        super().__init__()
        self.setFixedSize(46, 46)
        # Pick the SVG: subtype direct hit > event_type default > tourism.
        sub_key = (event_subtype or "").strip().lower()
        type_key = (event_type or "").strip().lower()
        icon_name = (
            self._SUBTYPE_TO_ICON.get(sub_key)
            or self._TYPE_DEFAULT.get(type_key)
            or "tourism"
        )
        icon_path = _CATEGORY_ICONS_DIR / f"{icon_name}.svg"
        self._icon_path = icon_path if icon_path.exists() else None
        # Tint: accent for open events, ink_soft for closed (recedes).
        self._tint = QColor("#8b94a7" if dim else "#7c6cff")
        # Both states share the card2 backdrop with no border. The icon
        # carries the semantic; the tile is a quiet holder.
        self.setStyleSheet(
            "background: #1e222d; border: none; border-radius: 13px;"
        )

    def paintEvent(self, evt) -> None:  # noqa: N802 — Qt override
        super().paintEvent(evt)
        if self._icon_path is None:
            return
        # Tile is 46x46 with no border + 13px radius. Render the 24x24
        # source SVG at 26x26 centered, matching the mockup .cat-emoji
        # ratio so the line family reads without crowding. The tint
        # path is the shared helper (spec/69 §3) — same SourceIn pattern,
        # cached per (path, size, color).
        from mira.ui.design.icons import tinted_svg_pixmap
        icon_size = 26
        pm = tinted_svg_pixmap(self._icon_path, icon_size, self._tint)
        if pm.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        x = (self.width() - icon_size) // 2
        y = (self.height() - icon_size) // 2
        p.drawPixmap(x, y, pm)
        p.end()


class _PhaseIcon(QLabel):
    """Tiny phase-name leading glyph for the open-card pipeline rows.

    16px line-icon (collect/pick/edit/export) tinted the phase's identity
    colour — the row reads as a real iconographic dashboard line instead of
    the mockup's Unicode emoji or the migration's bare text. Used 4× per
    open card; the tint helper caches per (path, size, colour) so theme
    toggles re-tint without re-rasterising the SVG.
    """

    _SIZE = 16

    def __init__(self, phase: str, color: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(self._SIZE + 2, self._SIZE + 2)
        path = PHASE_GLYPH.get(phase)
        if path is None:
            return
        pm = tinted_svg_pixmap(path, self._SIZE, QColor(color))
        if not pm.isNull():
            self.setPixmap(pm)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


class EventCardRedesign(Card):
    """The Surface 01 event tile (replaces the legacy EventCard visually)."""

    activated = pyqtSignal(str)
    title_clicked = pyqtSignal(str)
    info_clicked = pyqtSignal(str)
    plan_requested = pyqtSignal(str)
    status_toggled = pyqtSignal(str)

    def __init__(
        self,
        data: EventCardData,
        *,
        sample_pixmaps: list[QPixmap] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, padded=True)
        self._data = data
        self._sample_pixmaps = sample_pixmaps or []
        self.layout().setSpacing(12)
        self._build_header()
        if data.is_closed:
            self._build_closed_body()
        else:
            self._build_open_body()
        self.setMinimumHeight(220 if not data.is_closed else 320)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred
        )

    # ── header ──────────────────────────────────────────────────────────

    def _build_header(self) -> None:
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(_CategoryTile(
            self._data.event_type or "unclassified",
            self._data.event_subtype,
            dim=self._data.is_closed,
        ))

        # Title block
        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        first_row = QHBoxLayout()
        first_row.setSpacing(8)
        title = QLabel(self._data.name or "(untitled)")
        title.setObjectName("CardTitle")
        title.setCursor(Qt.CursorShape.PointingHandCursor)
        title.mousePressEvent = (
            lambda _evt: self.title_clicked.emit(self._data.event_id)
        )
        first_row.addWidget(title)
        type_tag = self._event_type_tag()
        if type_tag is not None:
            first_row.addWidget(type_tag)
        first_row.addStretch()
        title_box.addLayout(first_row)
        sub_bits = []
        year = _year_str(self._data.start_date)
        if year:
            sub_bits.append(year)
        if self._data.event_type and self._data.event_type != "unclassified":
            sub_bits.append(self._data.event_type.title())
        if self._data.event_subtype:
            sub_bits.append(self._data.event_subtype)
        if sub_bits:
            sub = QLabel(" · ".join(sub_bits))
            sub.setObjectName("Sub")
            title_box.addWidget(sub)
        row.addLayout(title_box, 1)

        # Status + day chip
        right = QVBoxLayout()
        right.setSpacing(6)
        right.setAlignment(Qt.AlignmentFlag.AlignRight)
        status_chip = (
            chip_closed("✓ Closed") if self._data.is_closed
            else chip_open("● Open")
        )
        status_chip.setCursor(Qt.CursorShape.PointingHandCursor)
        status_chip.mousePressEvent = (
            lambda _evt: self.status_toggled.emit(self._data.event_id)
        )
        right.addWidget(status_chip, 0, Qt.AlignmentFlag.AlignRight)
        if self._data.total_days:
            day_chip = chip_idle(f"{self._data.total_days} days")
            day_chip.setCursor(Qt.CursorShape.PointingHandCursor)
            day_chip.setToolTip("Open the event's Days Table")
            day_chip.mousePressEvent = (
                lambda _evt: self.plan_requested.emit(self._data.event_id)
            )
            right.addWidget(day_chip, 0, Qt.AlignmentFlag.AlignRight)
        row.addLayout(right)

        self.layout().addLayout(row)

    def _event_type_tag(self) -> QLabel | None:
        et = (self._data.event_type or "").lower()
        if et in ("trip", "session"):
            return tag(et.upper())
        return None

    # ── open body ───────────────────────────────────────────────────────

    def _build_open_body(self) -> None:
        body = QHBoxLayout()
        body.setSpacing(18)

        # Left: date column. FROM / date / ↓ / TO / date / (UTC OFFSET / value).
        # The vertical arrow + the column's right-border (added below as a
        # QFrame VLine) come straight from the mockup's `.dates` block —
        # they're what turns the column from "labels stacked" into "a
        # well-cleft date pair." Without them the column reads as one
        # undifferentiated stack.
        date_box = QVBoxLayout()
        date_box.setSpacing(2)
        from_label = QLabel("FROM")
        from_label.setObjectName("Micro")
        date_box.addWidget(from_label)
        date_box.addWidget(QLabel(
            self._data.start_date.isoformat() if self._data.start_date else "—"
        ))
        arrow = QLabel("↓")
        arrow.setObjectName("DateArrow")
        accent = self._palette_color("accent")
        arrow.setStyleSheet(
            f"color: {accent}; font-size: 14px; font-weight: 700;"
        )
        date_box.addSpacing(4)
        date_box.addWidget(arrow)
        date_box.addSpacing(2)
        to_label = QLabel("TO")
        to_label.setObjectName("Micro")
        date_box.addWidget(to_label)
        date_box.addWidget(QLabel(
            self._data.end_date.isoformat() if self._data.end_date else "—"
        ))
        if self._data.tz_display:
            tz_label = QLabel("UTC OFFSET")
            tz_label.setObjectName("Micro")
            date_box.addSpacing(6)
            date_box.addWidget(tz_label)
            tz_lbl = QLabel(self._data.tz_display.split("\n")[0])
            tz_lbl.setObjectName("Faint")
            date_box.addWidget(tz_lbl)
        date_box.addStretch()
        date_wrap = QWidget()
        date_wrap.setLayout(date_box)
        date_wrap.setMinimumWidth(120)
        date_wrap.setMaximumWidth(150)
        # Restore the legacy left-zone link to the Days Table dialog —
        # the legacy EventCard treated the dates column as its
        # "Plan / Days Table" door (mira/ui/base/event_card.py:706
        # "Left zone — dates + TZ → emits info_clicked. Opens the plan
        # editor"). The redesign kept the same door on the top-right
        # "N days" chip but lost the left-zone affordance; restoring it
        # here so users who internalised the legacy gesture still land
        # on the right place. Same plan_requested signal as the chip.
        date_wrap.setCursor(Qt.CursorShape.PointingHandCursor)
        date_wrap.setToolTip("Open the event's Days Table")
        date_wrap.mousePressEvent = (
            lambda _evt: self.plan_requested.emit(self._data.event_id)
        )
        body.addWidget(date_wrap, 0)

        # Vertical line separator between dates and pipeline. Mockup uses
        # `border-right` on the dates column for the same visual cleft.
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        sep.setObjectName("DatesSep")
        line_color = self._palette_color("line")
        sep.setStyleSheet(
            f"color: {line_color}; background: {line_color};"
            " border: none; max-width: 1px; min-width: 1px;"
        )
        body.addWidget(sep)

        # Right: 4-stage pipeline progress. No "PIPELINE" header — the mockup
        # doesn't carry one; the bars themselves are the section. Each row
        # gets a 16px line-icon glyph (spec/65 §2.1) tinted the phase colour
        # so the row reads "📥 Collect ━━━━━ 100%" with real iconography
        # instead of plain text.
        pipeline = QVBoxLayout()
        pipeline.setSpacing(8)
        pipeline.setContentsMargins(0, 2, 0, 0)
        for phase in _PHASES:
            row = QHBoxLayout()
            row.setSpacing(8)
            phase_color = self._palette_color(_PHASE_COLOR_TOKEN[phase])
            row.addWidget(_PhaseIcon(phase, phase_color))
            label = QLabel(_PHASE_LABEL[phase])
            label.setFixedWidth(48)
            row.addWidget(label)
            bar = StageProgress()
            percent, _state = _stage_value(
                self._data.status_by_phase.get(phase, {})
                if self._data.status_by_phase else None
            )
            bar.setValue(percent)
            # Bars encode PHASE (fixed colour), length encodes progress — no
            # done/in-progress state (spec/66: phases advance freely).
            bar.setColorToken(_PHASE_COLOR_TOKEN[phase])
            row.addWidget(bar, 1)
            pct_label = QLabel(f"{percent}%")
            # Percentage in the phase colour when there's progress, faint at 0%.
            pct_label.setObjectName(
                _PHASE_PCT_ROLE[phase] if percent > 0 else "PctZero"
            )
            pct_label.setMinimumWidth(40)
            pct_label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            row.addWidget(pct_label)
            pipeline.addLayout(row)
        pipeline_wrap = QWidget()
        pipeline_wrap.setLayout(pipeline)
        # Body click anywhere on the pipeline activates the event
        pipeline_wrap.setCursor(Qt.CursorShape.PointingHandCursor)
        pipeline_wrap.mousePressEvent = (
            lambda _evt: self.activated.emit(self._data.event_id)
        )
        body.addWidget(pipeline_wrap, 1)
        self.layout().addLayout(body)

    @staticmethod
    def _palette_color(token: str) -> str:
        return PALETTE[
            EventCardRedesign._palette_mode_str()
        ].get(token, "#7c6cff")

    @staticmethod
    def _palette_mode_str() -> str:
        app = QApplication.instance()
        return (app.property("theme") if app else None) or "dark"

    # ── closed body ─────────────────────────────────────────────────────

    def _build_closed_body(self) -> None:
        body = QHBoxLayout()
        body.setSpacing(18)

        # Left: carousel of exported sample photos. Min height tuned so a
        # 3:2 landscape final (the typical export aspect) gets a sensible
        # slot without the dots clipping into the photo.
        carousel = Carousel(self._sample_pixmaps, interval_ms=4000)
        carousel.setMinimumWidth(260)
        carousel.setMinimumHeight(200)
        # Click activates the event (route to Cuts list for closed events)
        carousel.mousePressEvent = (
            lambda _evt: self.activated.emit(self._data.event_id)
        )
        body.addWidget(carousel, 2)

        # Right: stats + classification chips
        right = QVBoxLayout()
        right.setSpacing(10)
        stats_grid = QGridLayout()
        stats_grid.setHorizontalSpacing(10)
        stats_grid.setVerticalSpacing(10)
        d = self._data
        collected = d.collected_count or 0
        picked = d.picked_count or 0
        edited = d.edited_count or 0
        exported = d.exported_count or 0
        # Stat-tile value colours — phase identity (spec/66): Collected
        # blue · Picked accent · Edited amber · Exported green. Read
        # from the live palette so the Collect cyan stays in sync with
        # the open card's pipeline bar.
        p = PALETTE[self._palette_mode_str()]
        rows = [
            ("Collected", str(collected), p["blue"], None),
            ("Picked", str(picked), p["accent"],
             f"· {int(picked / collected * 100)}%" if collected else None),
            ("Edited", str(edited), p["amber"],
             f"· {int(edited / picked * 100)}%" if picked else None),
            ("Exported", str(exported), p["green"],
             f"· {int(exported / picked * 100)}%" if picked else None),
        ]
        for col, (label, value, color, suffix) in enumerate(rows):
            stats_grid.addWidget(
                StatTile(label, value, value_color=color, suffix=suffix),
                col // 2, col % 2,
            )
        right.addLayout(stats_grid)

        # Classification chip strip — accent tags per category, capped at 6
        if d.classification_counts:
            chip_row = QHBoxLayout()
            chip_row.setSpacing(6)
            tags_sorted = sorted(
                d.classification_counts.items(),
                key=lambda t: t[1], reverse=True,
            )[:6]
            for cls, n in tags_sorted:
                chip_row.addWidget(tag(f"{cls} ×{n}"))
            chip_row.addStretch()
            right.addLayout(chip_row)
        right.addStretch()

        right_wrap = QWidget()
        right_wrap.setLayout(right)
        body.addWidget(right_wrap, 3)
        self.layout().addLayout(body)
