"""Surface 05 — Days Lists (per-day Picked/Skipped dashboard).

A 'pick where to start' surface that sits between Surface 03 (Phases) and
Surface 06 (Days Grid). Shows every event day as a card with two stacked
progress bars (green Picked / red Skipped) so the user can decide which
day to step into next.

This surface is a DASHBOARD, not capture-level — its bars use the design-
system progress styling (green = picked / red = skipped). The fixed
§5a photo-border semantics apply to Days Grid / Picker / Editor, not here.

Composition (design-system §Surface 05):
    Header row: ghost Back · title block (Pick where to start · "event · N days")
                · primary '+ Start a new pass…' · ghost '✓ Pick all days'
                · ghost '✗ Skip all days' (hover red).
    Body:       scrollable QScrollArea of DayRow cards. Each DayRow:
                  · accent day-number badge (left)
                  · title + date stacked on top of two StageProgress bars
                    (picked green, skipped red)
                  · per-row ✓ Pick all / ✗ Skip all mini ghost buttons
                  · meta column on the right (Buckets / Items)
                Clicking a row emits ``day_activated(day_number)`` so the
                host routes into the Days Grid for that day.

No legacy counterpart in the project — this is a new dashboard introduced
by the redesign. MainWindow integration lands when Surface 03's Pick tile
click is rerouted to land here (today it routes straight to PickPage).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import Gateway
from mira.ui.design import (
    Card,
    StageProgress,
    SurfaceIdentityHeader,
    danger_ghost_button,
    ghost_button,
    primary_button,
)
from mira.ui.i18n import tr
from mira.ui.palette import PALETTE


def _palette_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"

log = logging.getLogger(__name__)


@dataclass
class DaySnapshot:
    """One day's Pick / Skip totals + meta. The page builds these from
    gateway queries in the live path; mocked in the smoke path.

    ``capture_hours`` is a 24-int list — one count per hour-of-day — that
    drives the per-day capture spark micro-chart. Empty list = no
    capture-time data and the spark renders flat.
    """

    day_number: int
    title: str
    date_iso: str          # 'YYYY-MM-DD'
    picked: int = 0
    skipped: int = 0
    # Edit-phase numerator: picked keepers that are off the unedited
    # baseline (non-default look/crop/filter, per core.edit_status). Only
    # read when the page renders under the Edit identity (Nelson 2026-06-18).
    edited: int = 0
    # Export-phase numerators (spec/89 §4.1 / Block 3 D1.C three-slice
    # bar). All three sum to ``picked`` (the keepers denominator) when
    # the model is internally consistent. Only read when the page
    # renders under the Export identity.
    exported: int = 0           # green-intent items — will ship
    dropped_export: int = 0     # red-intent items — won't ship
    undecided: int = 0          # Compare-state cluster members (Slice 5)
    buckets: int = 0
    items: int = 0
    location: str = ""
    notes: list[str] = field(default_factory=list)
    capture_hours: list[int] = field(default_factory=lambda: [0] * 24)


class _DayBadge(QLabel):
    """Accent-soft tile carrying the day number — mockup `.num`: 40x40,
    12px radius, accent-soft bg + accent fg, **no border**. The badge was
    previously 46x46 with an accent border which read as a heavy chip;
    the mockup wants it quieter so the day-card's title block takes the
    visual lead. Colours pulled from the live palette so light theme picks
    up #eceaff instead of dark's #211f3a (per-surface bug class)."""

    def __init__(self, n: int) -> None:
        super().__init__(str(n))
        self.setObjectName("DayBadge")  # styled by redesign.qss — themes correctly
        self.setFixedSize(40, 40)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)


class _CaptureSpark(QWidget):
    """Tiny 24-hour capture-density spark — one vertical bar per hour.

    The "per-day analytic touches" §3.5 wants — without crowding the
    card. Heights are normalized to the day's peak so the spark reads
    even when one day captured 5 items and another 500. The golden-hour
    bands (5–8 AM and 5–8 PM) are tinted amber so the user reads when
    the day's good light hit even before parsing the bar heights.

    The widget paints from the active palette so theme toggles re-tint
    transparently."""

    _BAR_W = 5
    _GAP = 1
    _ICON_H = 14   # top strip for the sun / golden-sun / moon condition icons
    _LABEL_H = 12  # bottom strip for the 0h / 12h / 24h axis labels
    _SIZE = (24 * (5 + 1), 76)
    _GOLDEN_AM = range(5, 8)   # 5–7 AM (used for the tooltip golden total)
    _GOLDEN_PM = range(17, 20)  # 5–7 PM
    # Fixed light-condition bands (hour ranges) — no timezone / season. The
    # spark's BACKGROUND is tinted per band so night / golden / day read at a
    # glance; the capture-density bars sit on top in a single colour.
    _BANDS = (
        (0, 5, "night"),
        (5, 8, "golden"),
        (8, 17, "day"),
        (17, 20, "golden"),
        (20, 24, "night"),
    )

    def __init__(self, hours: list[int], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._hours = list(hours) if hours else [0] * 24
        if len(self._hours) < 24:
            self._hours = (self._hours + [0] * 24)[:24]
        self.setFixedSize(*self._SIZE)
        self.setToolTip(self._tooltip_text())

    def _tooltip_text(self) -> str:
        total = sum(self._hours)
        golden = sum(
            self._hours[h] for h in list(self._GOLDEN_AM) + list(self._GOLDEN_PM)
        )
        return (
            f"{total} captures across the day · "
            f"{golden} during golden hour (5–7 AM / 5–7 PM)"
        )

    def paintEvent(self, _evt) -> None:  # noqa: N802 — Qt override
        p = PALETTE[_palette_mode()]
        W, H = self.width(), self.height()
        step = self._BAR_W + self._GAP
        cw = 24 * step
        box_bottom = H - self._LABEL_H   # the box wraps the icons + chart
        chart_top = self._ICON_H         # bars start just below the icon strip
        chart_bottom = box_bottom - 1    # just inside the bottom border
        card = QColor(p["card"])
        amber = QColor(p.get("amber", "#fbbf24"))
        ink_soft = QColor(p["ink_soft"])
        ink_faint = QColor(p["ink_faint"])
        line = QColor(p["line"])

        painter = QPainter(self)
        painter.setPen(Qt.PenStyle.NoPen)

        # Card fill so the widget is never the global window-grey and the moon
        # crescent carves cleanly. No background tint — the icons name the zones
        # and thin dividers separate them (Nelson 2026).
        painter.setBrush(card)
        painter.drawRect(0, 0, W, H)

        # 1. Capture-density bars — one per hour, single colour, height
        # normalized to the day's peak.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        peak = max(self._hours) or 1
        bar_c = QColor(p["accent"])
        avail = chart_bottom - chart_top - 1
        for hour, n in enumerate(self._hours):
            if n <= 0:
                continue
            bh = max(2, int(round(n / peak * avail)))
            painter.setBrush(bar_c)
            painter.drawRect(hour * step, chart_bottom - bh, self._BAR_W, bh)

        # 2. Zone dividers (full box height) + a border that wraps the icons
        # AND the chart as one box (no line between the icons and the bars).
        painter.setBrush(line)
        for (h0, _h1, _k) in self._BANDS[1:]:
            painter.drawRect(h0 * step, 0, 1, box_bottom)
        painter.drawRect(0, 0, cw, 1)                  # top
        painter.drawRect(0, box_bottom - 1, cw, 1)     # bottom
        painter.drawRect(0, 0, 1, box_bottom)          # left
        painter.drawRect(cw - 1, 0, 1, box_bottom)     # right

        # 3. Condition icons in the top strip, centred over each zone.
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = 4.0
        for (h0, h1, kind) in self._BANDS:
            cx = ((h0 + h1) / 2.0) * step
            cy = self._ICON_H / 2.0
            if kind == "day":                       # full sun
                painter.setBrush(amber)
                painter.drawEllipse(QPointF(cx, cy), r, r)
            elif kind == "golden":                  # half sun on the horizon
                painter.setBrush(amber)
                painter.drawChord(
                    QRectF(cx - r, cy - r, 2 * r, 2 * r), 0, 180 * 16)
            else:                                   # crescent moon
                painter.setBrush(ink_soft)
                painter.drawEllipse(QPointF(cx, cy), r, r)
                painter.setBrush(card)
                painter.drawEllipse(QPointF(cx + r * 0.55, cy - r * 0.25), r, r)

        # 4. Hour-axis labels below the box: 0h · 12h · 24h.
        painter.setPen(ink_faint)
        f = QFont(self.font())
        f.setPixelSize(8)
        painter.setFont(f)
        align_v = Qt.AlignmentFlag.AlignVCenter
        painter.drawText(QRectF(0, box_bottom, 22, self._LABEL_H),
                         int(Qt.AlignmentFlag.AlignLeft | align_v), "0h")
        painter.drawText(QRectF(cw / 2 - 15, box_bottom, 30, self._LABEL_H),
                         int(Qt.AlignmentFlag.AlignHCenter | align_v), "12h")
        painter.drawText(QRectF(cw - 22, box_bottom, 22, self._LABEL_H),
                         int(Qt.AlignmentFlag.AlignRight | align_v), "24h")
        painter.end()


def _mini_button(label: str, color_token: str, tooltip: str) -> QPushButton:
    """A small inline ghost-style button used for per-row Pick all /
    Skip all. The mockup `.mini` styling: 4px 9px padding, 11.5px text,
    ink_soft default, hover picks up the semantic colour (green / red).
    Quieter than the full ghost_button so the rows stay readable when
    the user has 30+ days. Inline because this is a per-row affordance
    that doesn't deserve a top-level design-system role."""
    btn = QPushButton(label)
    btn.setObjectName(f"DayRowMini_{color_token}")
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip(tooltip)
    btn.setFlat(True)
    p = PALETTE[_palette_mode()]
    color_hex = p.get(color_token, p["ink_soft"])
    btn.setStyleSheet(  # pragma: no-qss — dynamic per-colour mini-button role
        f"QPushButton#DayRowMini_{color_token} {{"
        f"  background: transparent; color: {p['ink_soft']};"
        f"  border: 1px solid {p['line']}; border-radius: 8px;"
        f"  padding: 4px 10px; font-size: 11px; font-weight: 600;"
        " }"
        f"QPushButton#DayRowMini_{color_token}:hover {{"
        f"  border-color: {color_hex}; color: {color_hex};"
        " }"
    )
    return btn


class DayRow(Card):
    """One day card. Click anywhere fires :sig:`activated(day_number)`."""

    activated = pyqtSignal(int)
    pick_all_requested = pyqtSignal(int)
    skip_all_requested = pyqtSignal(int)

    def __init__(
        self,
        snapshot: DaySnapshot,
        parent: QWidget | None = None,
        phase: str = "pick",
    ) -> None:
        super().__init__(parent, padded=True)
        self._snapshot = snapshot
        # The host phase decides what each row MEASURES (spec/71 — the
        # shared Days Lists takes its host's identity). Under "edit" the
        # row swaps the Pick/Skip read for Picked (green, for continuity
        # with the previous phase) + Edited (amber), and drops the
        # per-row Pick/Skip verbs which don't apply in Edit
        # (Nelson 2026-06-18).
        self._phase = phase
        self._is_edit = phase == "edit"
        # spec/89 §4.1 — Export adds a three-slice bar (Will export /
        # Undecided / Set aside; user-chosen 2026-06-19) and relabels
        # Pick all / Skip all to Export all / Drop all. The underlying
        # signal wiring is the same as Pick — the host (main_window)
        # routes to the Edit phase_state ledger when
        # ``_export_phase_active`` is set.
        self._is_export = phase == "export"
        self.setMinimumHeight(120)
        # Fixed-height tile: never stretch vertically to fill the
        # container, regardless of how few rows there are (Nelson
        # 2026-06-22 — a single day used to balloon to the full viewport
        # height). Maximum vertical policy caps the card at its sizeHint.
        self.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum
        )
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # spec/131 — the row is a click target, so make it focus-able
        # so the host's ``ensure_day_visible`` highlight (focus ring)
        # actually paints on return from the Days Grid. StrongFocus
        # also keeps Tab cycling sensible on this surface.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        # Mockup `.day{padding:14px 16px}` — quieter than the legacy 16/14.
        self.layout().setContentsMargins(16, 14, 16, 14)
        self.layout().setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(14)

        # Left column — day number badge with the day's cluster + item counts
        # beneath it (Nelson 2026: moved here from the right meta so they read
        # with the day identity, not the spark).
        left = QVBoxLayout()
        left.setSpacing(4)
        left.setAlignment(Qt.AlignmentFlag.AlignTop)
        left.addWidget(_DayBadge(snapshot.day_number))
        _clusters = QLabel(f"Clusters · <b>{snapshot.buckets}</b>")
        _clusters.setObjectName("Sub")
        _clusters.setTextFormat(Qt.TextFormat.RichText)
        left.addWidget(_clusters)
        _items = QLabel(f"Items · <b>{snapshot.items}</b>")
        _items.setObjectName("Sub")
        _items.setTextFormat(Qt.TextFormat.RichText)
        left.addWidget(_items)
        left.addStretch()
        _left_wrap = QWidget()
        _left_wrap.setLayout(left)
        _left_wrap.setFixedWidth(98)
        row.addWidget(_left_wrap)

        # Center column: title + per-row actions + bars
        center = QVBoxLayout()
        center.setSpacing(7)
        top = QHBoxLayout()
        top.setSpacing(10)
        title_block = QHBoxLayout()
        title_block.setSpacing(8)
        title = QLabel(snapshot.title or f"Day {snapshot.day_number}")
        title.setObjectName("DayRowTitle")
        # Mockup `.info h3{font-size:14.5px;letter-spacing:-.2px}` — smaller
        # + tighter than CardTitle (18/700) so the day badge + title
        # together feel balanced, not heavy.
        f = QFont(title.font())
        f.setPixelSize(14)
        f.setWeight(QFont.Weight.DemiBold)
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.2)
        title.setFont(f)
        title.setMinimumWidth(0)
        # Ignored horizontal policy bypasses QLabel's text-width
        # minimumSizeHint — without it a long title pushes the whole
        # row's minimum past the viewport and the spark on the right
        # gets clipped off-screen (the QScrollArea has horizontal-
        # scrolling OFF). Same trick the bar-row labels use.
        title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        title_block.addWidget(title, 1)
        sub_bits = [snapshot.date_iso]
        if snapshot.location:
            sub_bits.append(snapshot.location)
        sub_text = " · ".join(b for b in sub_bits if b)
        if sub_text:
            sub = QLabel(f"· {sub_text}")
            sub.setObjectName("Faint")
            sub.setMinimumWidth(0)
            sub.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            title_block.addWidget(sub)
        top.addLayout(title_block, 1)
        # Per-row Pick all / Skip all — mockup `.mini` quiet buttons
        # instead of the noisy ghost_button cluster the migration used.
        # Pick verbs only: Edit has no per-day Skip, so the cluster is
        # omitted there and the title block takes the full row width.
        if not self._is_edit:
            if self._is_export:
                # spec/89 §4.1 — relabel for Export; the underlying signal
                # is the same bulk phase_state write the host routes to
                # the edit ledger (with respect-decisions semantics on
                # the host side per Block 3 D2a.B).
                pick_label = "✓ Export all"
                pick_tip = (
                    f"Set every undecided item on day {snapshot.day_number} "
                    "to Export. Explicit Drop decisions are kept."
                )
                skip_label = "✗ Drop all"
                skip_tip = (
                    f"Drop every undecided item on day {snapshot.day_number}. "
                    "Explicit Export decisions are kept."
                )
            else:
                pick_label = "✓ Pick all"
                pick_tip = (
                    f"Pick every undecided item on day {snapshot.day_number}."
                )
                skip_label = "✗ Skip all"
                skip_tip = (
                    f"Skip every undecided item on day {snapshot.day_number}."
                )
            pick_all = _mini_button(pick_label, "green", pick_tip)
            pick_all.clicked.connect(
                lambda: self.pick_all_requested.emit(snapshot.day_number)
            )
            top.addWidget(pick_all)
            skip_all = _mini_button(skip_label, "red", skip_tip)
            skip_all.clicked.connect(
                lambda: self.skip_all_requested.emit(snapshot.day_number)
            )
            top.addWidget(skip_all)
        center.addLayout(top)

        # Stacked progress bars — mockup `.bars`. The 60px label + flex
        # track + 96px count value column matches the mockup's
        # proportions at the wider card width Mira uses.
        #
        # The two bars are PHASE-DRIVEN:
        #   Pick → Picked (green) / Skipped (red), both over captured items
        #          (they need NOT sum to 100% — undecided items exist).
        #   Edit → As shot (green) + Edited (amber), the two HALVES of the
        #          picked keepers: As shot = picked − edited (still at the
        #          unedited baseline), Edited = off the baseline. Both over
        #          picked, so the two ALWAYS sum to 100% — As-shot% is
        #          derived as ``100 − Edited%`` so rounding can't break the
        #          complement (Nelson 2026-06-18).
        # ``token`` forces the fill colour live-per-theme via
        # StageProgress.setColorToken so green/amber stay phase-stable
        # regardless of the done/prog/skip state machine. Each spec carries
        # its own pre-computed ``pct`` and a ``has_data`` flag.
        if self._is_edit:
            picked = max(0, snapshot.picked)
            edited = max(0, min(picked, snapshot.edited))
            as_shot = picked - edited
            if picked > 0:
                edited_pct = int(round(edited / picked * 100))
                as_shot_pct = 100 - edited_pct
            else:
                edited_pct = as_shot_pct = 0
            bar_specs = (
                ("As shot", as_shot, as_shot_pct, picked > 0, "green"),
                ("Edited", edited, edited_pct, picked > 0, "amber"),
            )
        elif self._is_export:
            # spec/89 §4.1 + §11.3 polish — three-slice bar over SHIP
            # INTENTS (spec/89 §1.1), not picked keepers. A versions
            # cluster with two members contributes 2 to the bar; a flat
            # single-version cell contributes 1; a 0-version keeper
            # contributes 1 default-skipped. The gateway returns the
            # three counts pre-summed; the denominator here is just
            # ``shipped + undecided + dropped`` so cluster
            # multiplicity reads correctly.
            shipped = max(0, snapshot.exported)
            dropped = max(0, snapshot.dropped_export)
            undecided = max(0, snapshot.undecided)
            total = shipped + dropped + undecided
            if total > 0:
                shipped_pct = int(round(shipped / total * 100))
                undecided_pct = int(round(undecided / total * 100))
                dropped_pct = max(0, 100 - shipped_pct - undecided_pct)
            else:
                shipped_pct = undecided_pct = dropped_pct = 0
            bar_specs = (
                ("Will export", shipped, shipped_pct, total > 0, "green"),
                # spec/89 Block 1 D3.A / Block 4 D1.B — undecided uses
                # the Compare orange (distinct from Edit's amber so the
                # two phases never share a fill colour).
                ("Undecided", undecided, undecided_pct, total > 0, "compare"),
                ("Set aside", dropped, dropped_pct, total > 0, "red"),
            )
        else:
            items = snapshot.items
            total = max(1, items)
            bar_specs = (
                ("Picked", snapshot.picked,
                 int(round(snapshot.picked / total * 100)) if items > 0 else 0,
                 items > 0, "green"),
                ("Skipped", snapshot.skipped,
                 int(round(snapshot.skipped / total * 100)) if items > 0 else 0,
                 items > 0, "red"),
            )
        for label, count, pct, has_data, token in bar_specs:
            bar_row = QHBoxLayout()
            lab = QLabel(label)
            lab.setObjectName("DayRowBarLabel")
            # spec/129 — was setFixedWidth(60); the fixed-width labels
            # + fixed-width counts pinned every bar row above ~156px,
            # so the center column couldn't yield and the right-side
            # capture-distribution spark clipped when the dialog
            # narrowed. Switch to shrinkable widths capped at the
            # old visual budget. ``Ignored`` horizontal policy bypasses
            # QLabel's ``minimumSizeHint`` (which is the text's font-
            # metrics width); without it the layout can't go below
            # the per-label text width and the bar-row floor stays
            # near the old ~156px. With Ignored + min=0 + max=60 the
            # label rides at its preferred width when the row is wide
            # and gracefully clips its text when the row is narrow.
            # The compression order — track first via its 24px floor,
            # then label/count — leaves the spark fully visible at
            # every reasonable width.
            lab.setMinimumWidth(0)
            lab.setMaximumWidth(60)
            lab.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            bar_row.addWidget(lab)
            bar = StageProgress()
            bar.setValue(pct)
            bar.setColorToken(token if count > 0 else None)
            # Cap the track so the bars don't span the entire center
            # column at wide layouts — Nelson 2026: bars were eating
            # space the per-day capture-distribution spark needs to
            # breathe. The 240px ceiling keeps each bar comfortably
            # readable while leaving room beside the right-column
            # spark; the Expanding policy still lets the track absorb
            # leftover space up to the cap. Pairs with the title's
            # Ignored policy above so the row's minimum drops below
            # the viewport at narrow widths and the spark stays
            # visible (the QScrollArea has horizontal scroll OFF).
            bar.setMaximumWidth(240)
            bar_row.addWidget(bar, 1)
            count_label = QLabel(
                f"{count} ({pct}%)"
                if count > 0 else "—"
            )
            count_label.setObjectName("Faint" if count == 0 else "Sub")
            # spec/129 — same treatment as the label: was
            # setFixedWidth(96). Ignored policy + min=0 + max=96 lets
            # the count column give up width after the track, so the
            # spark never clips.
            count_label.setMinimumWidth(0)
            count_label.setMaximumWidth(96)
            count_label.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
            )
            count_label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            bar_row.addWidget(count_label)
            center.addLayout(bar_row)
        row.addLayout(center, 1)

        # Right column — the (now larger) capture spark on its own. The
        # Clusters / Items counts moved to the left column (Nelson 2026).
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        line_color = PALETTE[_palette_mode()]["line"]
        sep.setStyleSheet(  # pragma: no-qss — vertical separator, token colour
            f"color: {line_color}; background: {line_color};"
            " border: none; max-width: 1px; min-width: 1px;"
        )
        row.addWidget(sep)

        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        spark = _CaptureSpark(snapshot.capture_hours)
        spark_row = QHBoxLayout()
        spark_row.addStretch()
        spark_row.addWidget(spark)
        spark_row.addStretch()
        meta.addLayout(spark_row)
        meta_wrap = QWidget()
        meta_wrap.setLayout(meta)
        meta_wrap.setFixedWidth(168)
        row.addWidget(meta_wrap)

        # spec/129 — exposed for the layout test that pins the
        # "bars compress, spark stays" contract. Not part of the
        # public API.
        self._center_layout = center
        self._meta_wrap = meta_wrap
        self.layout().addLayout(row)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        super().mousePressEvent(e)
        self.activated.emit(self._snapshot.day_number)


_EXPORT_NOW_TIP_ALL_DAYS = (
    "Render every Will-export keeper across this event and unlink "
    "every Set-aside file from Exported Media/. Asks first."
)


class DaysListsPage(QWidget):
    """Surface 05 — per-day Picked/Skipped dashboard.

    Header signals route back to the host (Back / + New pass / global
    Pick-all / global Skip-all). Day-row signals route per-day actions.
    """

    back_requested = pyqtSignal()
    new_pass_requested = pyqtSignal()
    pick_all_days_requested = pyqtSignal()
    skip_all_days_requested = pyqtSignal()
    # spec/89 §5.1 D3.B — the all-days "Export now" run trigger.
    # Fired only in Export-identity mode; the host walks every day,
    # totals N + M, asks once, then submits per-day batches.
    export_now_requested = pyqtSignal()

    day_activated = pyqtSignal(int)               # day_number
    day_pick_all_requested = pyqtSignal(int)
    day_skip_all_requested = pyqtSignal(int)

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._event_id: Optional[str] = None
        self._event_name: str = ""
        self._snapshots: list[DaySnapshot] = []
        # spec/71 identity phase — drives the SurfaceIdentityHeader.
        # Defaults to ``"pick"``; the host calls
        # :meth:`set_phase_identity` for the Edit / Quick Sweep routes.
        self._identity_phase = "pick"
        self._identity: Optional[SurfaceIdentityHeader] = None
        # spec/131 — the last day the user activated from THIS list.
        # Hosts read it via :meth:`current_entry_anchor` as a fallback
        # restore anchor when the Days Grid reports no current day
        # (rare). The live restore path reads the grid's current day.
        self._entry_anchor_day_number: Optional[int] = None
        self._build_ui()

    def _build_ui(self) -> None:
        self.uses_titlebar_back = True  # Back lives in the shared title bar

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Full-width surface identity rail — same top chrome as every main
        # surface; phase-coloured via the host phase (accent under Pick, amber
        # under Edit, blue under Quick Sweep, green under Export). Nelson 2026.
        self._rail = QFrame()
        self._rail.setObjectName("SurfaceHeaderRail")
        self._rail.setFixedHeight(2)
        root.addWidget(self._rail)
        self._refresh_identity()  # sets the rail's phase colour

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(32, 18, 32, 24)
        outer.setSpacing(12)
        root.addWidget(content, 1)

        # ── TOP content band: scan chip + header (title + actions) ──
        top_band = QFrame()
        top_band.setObjectName("SurfaceBand")
        top_l = QVBoxLayout(top_band)
        top_l.setContentsMargins(16, 14, 16, 14)
        top_l.setSpacing(10)

        # External-edits scan chip (Export-only; toggled in _apply_phase_chrome).
        self._scan_chip = QLabel("External edits: up to date")
        self._scan_chip.setObjectName("Faint")
        self._scan_chip.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._scan_chip.setVisible(False)
        top_l.addWidget(self._scan_chip)

        # Header — mockup `.head` proportions: Back · title block · action
        # cluster. The title block follows `.ttl h1{font-size:22px;
        # letter-spacing:-.4px}` — smaller than the 30/800 PageTitle so
        # the per-event identity reads as a section header, not the
        # app-level brand. The "+ Start a new pass…" stays primary; the
        # Pick-all / Skip-all are intentionally ghost so the page doesn't
        # read as 3 hero CTAs side-by-side.
        head = QHBoxLayout()
        head.setSpacing(12)
        title_block = QVBoxLayout()
        title_block.setSpacing(2)
        self._title = QLabel("Pick where to start")
        self._title.setObjectName("DaysListsTitle")
        title_font = QFont(self._title.font())
        title_font.setPixelSize(22)
        title_font.setWeight(QFont.Weight.Black)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.4)
        self._title.setFont(title_font)
        title_block.addWidget(self._title)
        self._sub = QLabel("")
        self._sub.setObjectName("Sub")
        title_block.addWidget(self._sub)
        head.addLayout(title_block, 1)
        new_pass = primary_button("+ Start a new pass…")
        new_pass.clicked.connect(self.new_pass_requested.emit)
        head.addWidget(new_pass)
        # spec/89 §5.1 D3.B — the all-days Export now trigger. Visible
        # only under the Export identity; the host walks every day,
        # totals N + M, asks once with the locked modal, then submits
        # per-day batches through the spec/60 engine.
        self._export_now_btn = primary_button("↑ Export now")
        self._export_now_btn.setToolTip(_EXPORT_NOW_TIP_ALL_DAYS)
        self._export_now_btn.clicked.connect(
            self.export_now_requested.emit)
        self._export_now_btn.setVisible(False)
        head.addWidget(self._export_now_btn)
        # Global Pick-all / Skip-all are Pick verbs — hidden under the Edit
        # identity, where there is no day-level Skip (Nelson 2026-06-18).
        self._pick_all_days_btn = ghost_button("✓ Pick all days")
        self._pick_all_days_btn.clicked.connect(self.pick_all_days_requested.emit)
        head.addWidget(self._pick_all_days_btn)
        self._skip_all_days_btn = danger_ghost_button("✗ Skip all days")
        self._skip_all_days_btn.clicked.connect(self.skip_all_days_requested.emit)
        head.addWidget(self._skip_all_days_btn)
        self._apply_phase_chrome()
        top_l.addLayout(head)
        outer.addWidget(top_band)

        outer.addSpacing(8)

        # ── BOTTOM content band: the day-row list ──
        bottom_band = QFrame()
        bottom_band.setObjectName("SurfaceBand")
        bottom_l = QVBoxLayout(bottom_band)
        bottom_l.setContentsMargins(16, 16, 16, 16)
        bottom_l.setSpacing(0)
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        inner = QWidget()
        self._rows = QVBoxLayout(inner)
        self._rows.setContentsMargins(0, 0, 0, 0)
        self._rows.setSpacing(12)
        self._rows.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._scroll.setWidget(inner)
        self._scroll.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        bottom_l.addWidget(self._scroll)
        outer.addWidget(bottom_band, 1)

    # ── spec/71 identity header ────────────────────────────────────────

    _IDENTITY_SPEC = {
        "collect": ("Quick Sweep", "Choose a day to sweep"),
        "pick":    ("Pick",        "Pick where to start — choose a day"),
        "edit":    ("Edit",        "Edit where to start — choose a day"),
        "export":  ("Export",      "Choose what ships"),
    }

    def _refresh_identity(self) -> None:
        """Recolour the full-width rail for the current host phase. The spec/71
        identity is now carried by the rail colour + the section title (the
        badge/purpose/legend strip was dropped to match the other surfaces'
        plain top rail — Nelson 2026)."""
        rail = getattr(self, "_rail", None)
        if rail is None:
            return
        rail.setProperty("phase", self._identity_phase)
        rail.style().unpolish(rail)
        rail.style().polish(rail)

    def set_scan_status(self, report) -> None:
        """spec/89 §2.2 — push the spec/57 §3 return-scan report to the
        Export scan chip. The chip is auto-hidden under non-Export
        identities; this just updates the text so the next show reads
        the latest run."""
        try:
            from core.export_provenance import scan_chip_text
            self._scan_chip.setText(scan_chip_text(report))
        except Exception:                                          # noqa: BLE001
            log.exception("DaysListsPage: scan_chip_text failed")

    def set_phase_identity(self, phase: str) -> None:
        """Override the identity-header phase. Hosts call this before
        showing the page so Pick / Edit / Quick Sweep route to their own
        chrome (spec/71). Valid: ``"collect" / "pick" / "edit" / "export"``."""
        if phase in self._IDENTITY_SPEC and phase != self._identity_phase:
            self._identity_phase = phase
            self._refresh_identity()
            self._apply_phase_chrome()
            # Rows measure per-phase, so re-render if data is already loaded.
            if self._snapshots:
                self._render()

    def _apply_phase_chrome(self) -> None:
        """Show/hide phase-specific header verbs + relabel for Export.

        * Pick / Collect: ``Pick all days`` / ``Skip all days`` visible.
        * Edit: bulk verbs hidden — Edit is creative-only per spec/66
          §1.1 (Nelson 2026-06-18).
        * Export: bulk verbs visible but relabelled per spec/89 §4.1 to
          ``Export all days`` / ``Drop all days``; the host respects
          explicit P/X decisions on the bulk (Block 3 D2a.B).
        """
        # The scan chip + Export now button are Export-only.
        try:
            self._scan_chip.setVisible(self._identity_phase == "export")
        except AttributeError:
            pass  # During the very first _apply_phase_chrome in __init__
        try:
            self._export_now_btn.setVisible(
                self._identity_phase == "export")
        except AttributeError:
            pass
        if self._identity_phase == "export":
            self._pick_all_days_btn.setText("✓ Export all days")
            self._pick_all_days_btn.setToolTip(
                "Set every undecided picked keeper to Export. "
                "Explicit Drop decisions are kept.")
            self._skip_all_days_btn.setText("✗ Drop all days")
            self._skip_all_days_btn.setToolTip(
                "Drop every undecided picked keeper. "
                "Explicit Export decisions are kept.")
            self._pick_all_days_btn.setVisible(True)
            self._skip_all_days_btn.setVisible(True)
            return
        # Pick / Collect labels (Edit hides both).
        self._pick_all_days_btn.setText("✓ Pick all days")
        self._pick_all_days_btn.setToolTip("")
        self._skip_all_days_btn.setText("✗ Skip all days")
        self._skip_all_days_btn.setToolTip("")
        show_pick_verbs = self._identity_phase in ("pick", "collect")
        self._pick_all_days_btn.setVisible(show_pick_verbs)
        self._skip_all_days_btn.setVisible(show_pick_verbs)

    # ── data ────────────────────────────────────────────────────────────

    def setEventForPreview(
        self,
        event_name: str,
        snapshots: list[DaySnapshot],
        *,
        anchor_day_number: Optional[int] = None,
    ) -> None:
        """spec/131 — ``anchor_day_number``: after the day cards are
        built, scroll so that ``DayRow`` is visible + give it focus
        (highlight). Graceful when no row matches — falls through to
        the default "top" landing."""
        self._event_name = event_name
        self._snapshots = list(snapshots)
        self._render()
        if anchor_day_number is not None:
            self.ensure_day_visible(anchor_day_number)

    def ensure_day_visible(
        self, day_number: int, *, select: bool = True,
    ) -> bool:
        """spec/131 — scroll the inner viewport so the ``DayRow`` for
        ``day_number`` is visible. When ``select=True`` (default) the
        row gets keyboard focus too (visible highlight ring). Returns
        ``True`` when the row was found + the scroll/highlight applied;
        ``False`` when no row matches (graceful — host should fall
        through to the top landing)."""
        row = self._find_day_row(day_number)
        if row is None:
            return False
        try:
            self._scroll.ensureWidgetVisible(row)
            if select:
                row.setFocus(Qt.FocusReason.OtherFocusReason)
        except RuntimeError:
            log.debug(
                "DaysListsPage.ensure_day_visible: row %s gone",
                day_number, exc_info=True)
            return False
        return True

    def _find_day_row(self, day_number: int) -> Optional[DayRow]:
        for i in range(self._rows.count()):
            item = self._rows.itemAt(i)
            w = item.widget() if item is not None else None
            if isinstance(w, DayRow) and w._snapshot.day_number == day_number:
                return w
        return None

    def current_entry_anchor(self) -> Optional[int]:
        """spec/131 — last day the user activated from this list. Host
        falls back to this when the Days Grid reports no current day."""
        return self._entry_anchor_day_number

    def _on_day_row_activated(self, day_number: int) -> None:
        """spec/131 — store the entry anchor + forward the signal."""
        self._entry_anchor_day_number = int(day_number)
        self.day_activated.emit(int(day_number))

    def _render(self) -> None:
        self._sub.setText(
            f"{self._event_name} · {len(self._snapshots)} day"
            + ("" if len(self._snapshots) == 1 else "s")
        )
        while self._rows.count():
            it = self._rows.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        for snap in self._snapshots:
            row = DayRow(snap, phase=self._identity_phase)
            # spec/131 — intercept activation to record the entry
            # anchor (so the host has a fallback restore target if the
            # grid later reports no current day).
            row.activated.connect(self._on_day_row_activated)
            row.pick_all_requested.connect(self.day_pick_all_requested.emit)
            row.skip_all_requested.connect(self.day_skip_all_requested.emit)
            self._rows.addWidget(row)
        # Trailing stretch keeps the rows at their fixed height anchored to
        # the top. AlignTop alone doesn't stop Preferred-policy DayRow cards
        # from filling the scroll viewport, so a single day used to stretch
        # to occupy all the available vertical space (Nelson 2026-06-22).
        # Same fix the Share Cuts list already carries.
        self._rows.addStretch(1)
