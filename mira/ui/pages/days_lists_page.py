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

from PyQt6.QtCore import Qt, pyqtSignal
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
        self.setFixedSize(40, 40)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p = PALETTE[_palette_mode()]
        self.setStyleSheet(
            f"background: {p['accent_soft']}; color: {p['accent']};"
            " border: none; border-radius: 12px;"
            " font-size: 15px; font-weight: 800;"
        )


class _CaptureSpark(QWidget):
    """Tiny 24-hour capture-density spark — one vertical bar per hour.

    The "per-day analytic touches" §3.5 wants — without crowding the
    card. Heights are normalized to the day's peak so the spark reads
    even when one day captured 5 items and another 500. The golden-hour
    bands (5–8 AM and 5–8 PM) are tinted amber so the user reads when
    the day's good light hit even before parsing the bar heights.

    The widget paints from the active palette so theme toggles re-tint
    transparently."""

    _BAR_W = 3
    _GAP = 1
    _SIZE = (24 * 3 + 23, 28)
    _GOLDEN_AM = range(5, 8)   # 5–7 AM
    _GOLDEN_PM = range(17, 20)  # 5–7 PM

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
        track = QColor(p["track"])
        accent = QColor(p["accent"])
        amber = QColor(p.get("amber", "#fbbf24"))
        peak = max(self._hours) or 1
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(Qt.PenStyle.NoPen)
        x = 0
        for hour, n in enumerate(self._hours):
            h_norm = max(2, int(round(n / peak * self.height())))
            y = self.height() - h_norm
            track_rect_h = self.height() - 2
            # Track tick — a 2px floor at the bottom so empty hours still
            # show the day's outline.
            painter.setBrush(track)
            painter.drawRect(x, self.height() - 2, self._BAR_W, 2)
            if n > 0:
                is_golden = hour in self._GOLDEN_AM or hour in self._GOLDEN_PM
                painter.setBrush(amber if is_golden else accent)
                painter.drawRect(x, y, self._BAR_W, h_norm)
            x += self._BAR_W + self._GAP
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
    btn.setStyleSheet(
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
        # spec/89 §4.1 — Export adds a three-slice bar (Shipped /
        # Undecided / Dropped) and relabels Pick all / Skip all to
        # Export all / Drop all. The underlying signal wiring is the
        # same as Pick — the host (main_window) routes to the Edit
        # phase_state ledger when ``_export_phase_active`` is set.
        self._is_export = phase == "export"
        self.setMinimumHeight(120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        # Mockup `.day{padding:14px 16px}` — quieter than the legacy 16/14.
        self.layout().setContentsMargins(16, 14, 16, 14)
        self.layout().setSpacing(8)

        row = QHBoxLayout()
        row.setSpacing(14)

        # Day badge (40x40, no border per mockup)
        row.addWidget(_DayBadge(snapshot.day_number))

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
        title_block.addWidget(title, 1)
        sub_bits = [snapshot.date_iso]
        if snapshot.location:
            sub_bits.append(snapshot.location)
        sub_text = " · ".join(b for b in sub_bits if b)
        if sub_text:
            sub = QLabel(f"· {sub_text}")
            sub.setObjectName("Faint")
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
            # spec/89 §4.1 / Block 3 D1.C — three-slice bar over picked
            # keepers. The three counts always sum to picked under the
            # gateway's intent inference (Block 1 defaults + explicit
            # decisions); the orange slice fills only when versions
            # clusters carry Compare-state members (Slice 5+).
            picked = max(0, snapshot.picked)
            shipped = max(0, min(picked, snapshot.exported))
            dropped = max(0, min(picked - shipped, snapshot.dropped_export))
            undecided = max(0, picked - shipped - dropped)
            if picked > 0:
                shipped_pct = int(round(shipped / picked * 100))
                undecided_pct = int(round(undecided / picked * 100))
                dropped_pct = max(0, 100 - shipped_pct - undecided_pct)
            else:
                shipped_pct = undecided_pct = dropped_pct = 0
            bar_specs = (
                ("Shipped", shipped, shipped_pct, picked > 0, "green"),
                # spec/89 Block 1 D3.A / Block 4 D1.B — undecided uses
                # the Compare orange (distinct from Edit's amber so the
                # two phases never share a fill colour).
                ("Undecided", undecided, undecided_pct, picked > 0, "compare"),
                ("Dropped", dropped, dropped_pct, picked > 0, "red"),
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
            lab.setFixedWidth(60)
            bar_row.addWidget(lab)
            bar = StageProgress()
            bar.setValue(pct)
            bar.setColorToken(token if count > 0 else None)
            bar_row.addWidget(bar, 1)
            count_label = QLabel(
                f"{count} ({pct}%)"
                if count > 0 else "—"
            )
            count_label.setObjectName("Faint" if count == 0 else "Sub")
            count_label.setFixedWidth(96)
            count_label.setAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            bar_row.addWidget(count_label)
            center.addLayout(bar_row)
        row.addLayout(center, 1)

        # Right meta column — vertical separator + Buckets / Items + spark.
        # The mockup `.meta` is just two lines; the capture spark below
        # is the "analytic touch" §3.5 asks for. Golden-hour bands tint
        # amber so the user reads the day's golden-hour density at a
        # glance.
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.VLine)
        line_color = PALETTE[_palette_mode()]["line"]
        sep.setStyleSheet(
            f"color: {line_color}; background: {line_color};"
            " border: none; max-width: 1px; min-width: 1px;"
        )
        row.addWidget(sep)

        meta = QVBoxLayout()
        meta.setContentsMargins(0, 0, 0, 0)
        meta.setSpacing(2)
        meta.setAlignment(Qt.AlignmentFlag.AlignTop)
        clusters_label = QLabel(
            f"Clusters · <b>{snapshot.buckets}</b>"
        )
        clusters_label.setObjectName("Sub")
        clusters_label.setTextFormat(Qt.TextFormat.RichText)
        clusters_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        meta.addWidget(clusters_label)
        items_label = QLabel(f"Items · <b>{snapshot.items}</b>")
        items_label.setObjectName("Sub")
        items_label.setTextFormat(Qt.TextFormat.RichText)
        items_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        meta.addWidget(items_label)
        spark = _CaptureSpark(snapshot.capture_hours)
        meta.addSpacing(4)
        spark_row = QHBoxLayout()
        spark_row.addStretch()
        spark_row.addWidget(spark)
        meta.addLayout(spark_row)
        meta_wrap = QWidget()
        meta_wrap.setLayout(meta)
        meta_wrap.setMinimumWidth(110)
        row.addWidget(meta_wrap)

        self.layout().addLayout(row)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        super().mousePressEvent(e)
        self.activated.emit(self._snapshot.day_number)


class DaysListsPage(QWidget):
    """Surface 05 — per-day Picked/Skipped dashboard.

    Header signals route back to the host (Back / + New pass / global
    Pick-all / global Skip-all). Day-row signals route per-day actions.
    """

    back_requested = pyqtSignal()
    new_pass_requested = pyqtSignal()
    pick_all_days_requested = pyqtSignal()
    skip_all_days_requested = pyqtSignal()

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
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(16)

        # spec/71 identity header — the SHARED Days Lists inherits its
        # host phase's colour (accent under Pick, amber under Edit, blue
        # under Quick Sweep). Rebuilt on each :meth:`set_phase_identity`.
        self._identity_host = QWidget()
        self._identity_host_layout = QVBoxLayout(self._identity_host)
        self._identity_host_layout.setContentsMargins(0, 0, 0, 0)
        self._identity_host_layout.setSpacing(0)
        outer.addWidget(self._identity_host)
        self._refresh_identity()

        # spec/89 §2.2 — the external-edits scan chip mirrors the
        # legend-row chip on the Days Grid. Slice 2 ships the skeleton
        # with a static "up to date" reading; Slice 4 wires it to the
        # real scanner output (per-source breakdown on change).
        self._scan_chip = QLabel("External edits: up to date")
        self._scan_chip.setObjectName("Faint")
        self._scan_chip.setAlignment(Qt.AlignmentFlag.AlignLeft)
        self._scan_chip.setVisible(False)
        outer.addWidget(self._scan_chip)

        # Header — mockup `.head` proportions: Back · title block · action
        # cluster. The title block follows `.ttl h1{font-size:22px;
        # letter-spacing:-.4px}` — smaller than the 30/800 PageTitle so
        # the per-event identity reads as a section header, not the
        # app-level brand. The "+ Start a new pass…" stays primary; the
        # Pick-all / Skip-all are intentionally ghost so the page doesn't
        # read as 3 hero CTAs side-by-side.
        head = QHBoxLayout()
        head.setSpacing(12)
        self._back = ghost_button("‹ Back")
        self._back.clicked.connect(self.back_requested.emit)
        head.addWidget(self._back)
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
        # Global Pick-all / Skip-all are Pick verbs — hidden under the Edit
        # identity, where there is no day-level Skip (Nelson 2026-06-18).
        self._pick_all_days_btn = ghost_button("✓ Pick all days")
        self._pick_all_days_btn.clicked.connect(self.pick_all_days_requested.emit)
        head.addWidget(self._pick_all_days_btn)
        self._skip_all_days_btn = danger_ghost_button("✗ Skip all days")
        self._skip_all_days_btn.clicked.connect(self.skip_all_days_requested.emit)
        head.addWidget(self._skip_all_days_btn)
        self._apply_phase_chrome()
        outer.addLayout(head)

        # Day rows scroll
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
        outer.addWidget(self._scroll, 1)

    # ── spec/71 identity header ────────────────────────────────────────

    _IDENTITY_SPEC = {
        "collect": ("Quick Sweep", "Choose a day to sweep"),
        "pick":    ("Pick",        "Pick where to start — choose a day"),
        "edit":    ("Edit",        "Edit where to start — choose a day"),
        "export":  ("Export",      "Choose what ships"),
    }

    def _refresh_identity(self) -> None:
        """(Re)build the SurfaceIdentityHeader for the current host phase."""
        if self._identity is not None:
            self._identity_host_layout.removeWidget(self._identity)
            self._identity.deleteLater()
            self._identity = None
        name, purpose = self._IDENTITY_SPEC.get(
            self._identity_phase, self._IDENTITY_SPEC["pick"])
        self._identity = SurfaceIdentityHeader(
            phase=self._identity_phase,
            name=tr(name),
            purpose=tr(purpose),
        )
        self._identity_host_layout.addWidget(self._identity)

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
        # The scan chip is Export-only.
        try:
            self._scan_chip.setVisible(self._identity_phase == "export")
        except AttributeError:
            pass  # During the very first _apply_phase_chrome in __init__
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
    ) -> None:
        self._event_name = event_name
        self._snapshots = list(snapshots)
        self._render()

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
            row.activated.connect(self.day_activated.emit)
            row.pick_all_requested.connect(self.day_pick_all_requested.emit)
            row.skip_all_requested.connect(self.day_skip_all_requested.emit)
            self._rows.addWidget(row)
