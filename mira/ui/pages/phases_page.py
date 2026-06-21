"""Surface 03 — Phases (Collect / Pick / Edit / Export donut cards).

Per-event overview that lands when the user opens an event from the events
list. Replaces the legacy :class:`~mira.ui.pages.activity_dashboard_page.ActivityDashboardPage`
visually; the data layer (per-phase gateway queries) stays as-is and the
phase-tile signals match so MainWindow routing doesn't change.

Composition (design-system surface-03 spec):
    Header row: ghost Back · category icon tile · event title (CardTitle)
                · meta breadcrumb in ink_soft · StatusPill pushed right.
    2x2 grid:   PhaseCard × 4 — Collect / Pick / Edit / Share. Each card
                is a #Card with a step badge + phase name + status chip,
                a 140px Donut (filled for active, hollow with state word
                for not-started), and a muted caption explaining the
                metric. Clicking a card emits phase_tile_activated(phase).

The page is gateway-aware (real data path) and also exposes
``setEventForPreview(meta, phase_data)`` so the smoke renders without
running the full app.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QVBoxLayout,
    QWidget,
)

from mira.gateway import Gateway
from mira.ui.design import (
    Card,
    Card2,
    Donut,
    DonutSlice,
    chip_closed,
    chip_idle,
    chip_open,
    ghost_button,
)
from mira.ui.pages._event_card_redesign import _CategoryTile
from mira.ui.palette import PALETTE

log = logging.getLogger(__name__)


@dataclass
class PhaseSnapshot:
    """One phase's donut payload + status. Computed by the page from the
    gateway in the live path; mocked directly in the smoke path."""

    key: str            # "collect" | "pick" | "edit" | "share"
    label: str          # "Collect" | "Pick" | "Edit" | "Share"
    status: str         # "done" | "prog" | "idle" | "skipped"
    slices: list[DonutSlice]
    center_text: str = ""
    center_sub: str = ""
    state_word: str = ""  # for idle/skipped: overrides slices with empty ring
    # Numerator / denominator preserved across the empty-state path so the
    # hero summary banner can read "0 / 185 edited" even when slices=[].
    # The donut itself still uses slices/state_word to decide rendering.
    numerator: int = 0
    denominator: int = 0


@dataclass
class EventMeta:
    """Header / breadcrumb data fed to PhasesPage."""

    event_id: str
    name: str
    event_type: str = "trip"
    event_subtype: str | None = None
    is_closed: bool = False
    total_days: int = 0
    start_date: str = ""
    end_date: str = ""
    tz_display: str = ""
    location: str = ""


_PHASE_CAPTIONS = {
    "collect": "Per-camera contribution to the captured running time.",
    "pick":    "Share of captures reviewed (picked or skipped).",
    "edit":    "Share of picks that have been edited (developed).",
    "export":  "Share of picks materialised to an exported file.",
}

_PHASE_ORDER = ("collect", "pick", "edit", "export")

# Per-phase identity colours (spec/66) — same as the events-card pipeline +
# closed-card stat tiles: Collect blue · Pick accent · Edit amber · Export green.
_PHASE_FILL_TOKEN = {
    "collect": "blue", "pick": "accent", "edit": "amber", "export": "green",
}


def _palette_mode() -> str:
    app = QApplication.instance()
    return (app.property("theme") if app else None) or "dark"


def _phase_status_chip(status: str, phase_key: str) -> QLabel:
    """One-glance .ph-row status. Mockup `.st.done` is BLUE — the spec/66
    rule reads the "done" colour as the phase's identity colour, so each
    card's chip carries that phase's hue (Collect blue · Pick accent · Edit
    amber · Export green). Not-started uses the quiet card2 chip; in-progress
    inherits the prog amber + tinted background so the user sees motion at
    the top of the card without scanning the donut.
    """
    p = PALETTE[_palette_mode()]
    if status == "done":
        token = _PHASE_FILL_TOKEN.get(phase_key, "accent")
        color = p.get(token, p["accent"])
        chip = QLabel("Done")
        chip.setObjectName("ChipPhaseDone")
        chip.setStyleSheet(  # pragma: no-qss — phase-coloured chip, colour is data-driven
            f"background: {_with_alpha(color, 36)}; color: {color};"
            " border-radius: 13px; padding: 4px 11px; font-size: 11px;"
            " font-weight: 700;"
        )
        return chip
    if status == "prog":
        amber = p.get("amber", "#fbbf24")
        chip = QLabel("In progress")
        chip.setObjectName("ChipPhaseProg")
        chip.setStyleSheet(  # pragma: no-qss — phase-coloured chip, colour is data-driven
            f"background: {_with_alpha(amber, 36)}; color: {amber};"
            " border-radius: 13px; padding: 4px 11px; font-size: 11px;"
            " font-weight: 700;"
        )
        return chip
    if status == "skipped":
        return chip_closed("Skipped")
    return chip_idle("Not started")


def _with_alpha(hex_color: str, alpha255: int) -> str:
    """Turn ``#rrggbb`` + 0..255 alpha into the ``rgba(r,g,b,a)`` form QSS
    accepts. Tiny helper so the chip stylesheet stays readable."""
    c = QColor(hex_color)
    return (
        f"rgba({c.red()},{c.green()},{c.blue()},{alpha255 / 255:.3f})"
    )


class _StepBadge(QLabel):
    """Small numbered chip — 1..4 in accent_soft / accent.

    Borderless per the surface-03 mockup (.step rule): the accent_soft
    backdrop carries enough visual weight without an outline. Square-ish
    rounded rect at 8px radius matches the mockup's `border-radius: 8px`.
    The colours are pulled from the active palette so light-theme renders
    pick up the right accent-soft (#eceaff) instead of dark's #211f3a.
    """

    def __init__(self, n: int) -> None:
        super().__init__(str(n))
        self.setFixedSize(24, 24)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        p = PALETTE[_palette_mode()]
        self.setStyleSheet(  # pragma: no-qss — accent-soft tile, token colours
            f"background: {p['accent_soft']}; color: {p['accent']};"
            " border: none; border-radius: 8px;"
            " font-size: 12px; font-weight: 800;"
        )


class _DonutLegend(QWidget):
    """Vertical key shown to the right of the Collect donut.

    One row per slice: `[11px swatch] [name in ink_soft] [bold % in ink]`.
    Spec/65 §3.3's biggest legibility gap — without this, the per-camera
    color slices are unreadable. Mockup .legend rules:
        gap: 8px, font-size: 12.5px, swatch 11x11 rounded 3px.

    Driven directly from a :class:`PhaseSnapshot.slices` list — total is
    re-summed so the percentages match the painted arcs.
    """

    def __init__(
        self, slices: list[DonutSlice], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        total = sum(max(0.0, s.value) for s in slices) or 1.0
        for s in slices:
            if s.value <= 0:
                continue
            v.addLayout(self._row(s, total))
        v.addStretch()

    @staticmethod
    def _row(s: DonutSlice, total: float) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        row.setContentsMargins(0, 0, 0, 0)
        swatch = QFrame()
        swatch.setFixedSize(11, 11)
        swatch.setStyleSheet(  # pragma: no-qss — camera swatch colour is data-driven
            f"background: {s.color}; border-radius: 3px;"
        )
        row.addWidget(swatch)
        name = QLabel(s.label)
        name.setObjectName("Sub")
        name.setMinimumWidth(86)  # fixed name column so the %s align in a column
        row.addWidget(name)
        row.addSpacing(12)        # small fixed gap before the % (not a wide stretch)
        pct = QLabel(f"{int(round(s.value / total * 100))}%")
        # The legend percentage reads from the live palette so light-theme
        # renders use the dark ink colour (#1a1f2b) instead of dark's
        # #eef1f7 — the legend was previously invisible in light mode.
        # (No `font-variant-numeric: tabular-nums` — Qt QSS doesn't know
        # the CSS3 property and logs a warning per repaint. The values
        # here are short %s so monospacing isn't structurally needed.)
        p = PALETTE[_palette_mode()]
        pct.setStyleSheet(  # pragma: no-qss — token colour label
            f"color: {p['ink']}; font-weight: 600;"
        )
        row.addWidget(pct)
        return row


class PhaseCard(Card):
    """One phase card. Click anywhere fires :sig:`activated(phase_key)`."""

    activated = pyqtSignal(str)

    def __init__(
        self,
        step: int,
        snapshot: PhaseSnapshot,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, padded=True)
        self._snapshot = snapshot
        # Heavier 2px card_border edge to match the event tiles (Nelson 2026).
        self.setProperty("strongBorder", True)
        # Compacter cards so the 2x2 sits comfortably inside the bottom
        # #SurfaceBand instead of stretching to fill the page (Nelson 2026).
        self.setMinimumHeight(210)
        self.layout().setSpacing(10)

        # Top row: step badge + phase label + status chip. The chip is the
        # at-a-glance signal the mockup carries (.st.done / .st.idle in
        # surface-03-phases.html) — the donut speaks the "how much," the
        # chip speaks the "where in the workflow." Phase-identity colour
        # on the Done chip (spec/66) ties this to the per-event cards on
        # Surface 01 + the closed-card stat tiles.
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(_StepBadge(step))
        title = QLabel(snapshot.label)
        title.setObjectName("CardTitle")
        head.addWidget(title)
        head.addStretch()
        self.layout().addLayout(head)

        # Body: donut (+ legend for multi-slice phases like Collect)
        body = QHBoxLayout()
        body.setSpacing(18)
        body.setContentsMargins(0, 6, 0, 0)
        donut = Donut()
        if snapshot.state_word:
            # Empty state — small hollow ring (120px), no slices, no legend.
            # Distinct from active phase donuts which dominate the card body.
            donut.setEmptyState(snapshot.state_word)
            # Same size as the active donuts so "Not started" tiles match the
            # height of the populated ones — even 2x2 rows (Nelson 2026).
            donut.setFixedSize(132, 132)
            body.addStretch()
            body.addWidget(donut, 0, Qt.AlignmentFlag.AlignCenter)
            body.addStretch()
        else:
            donut.setSlices(snapshot.slices)
            donut.setCenterText(snapshot.center_text, snapshot.center_sub)
            donut.setFixedSize(132, 132)
            # Multi-slice phases (Collect: per-camera) get a legend right
            # of the donut so the slice colors are legible. Single-fill
            # phases (Pick/Edit/Share: numerator vs track) skip the legend
            # and center the donut — the center "62% · 412/1084" already
            # speaks the metric, so empty space looks better symmetric.
            if self._wants_legend(snapshot):
                # Center the donut + compact legend as a group so the donut
                # isn't pushed to the left edge (Nelson 2026). The legend takes
                # its natural width instead of stretching the % to the far edge.
                body.addStretch()
                body.addWidget(donut, 0, Qt.AlignmentFlag.AlignCenter)
                body.addWidget(_DonutLegend(snapshot.slices))
                body.addStretch()
            else:
                body.addStretch()
                body.addWidget(donut, 0, Qt.AlignmentFlag.AlignCenter)
                body.addStretch()
        self.layout().addLayout(body, 1)

        # Per-phase "to go" / completion delta. Renders for the ratio
        # phases (Pick/Edit/Export) so the user reads the *gap* the donut
        # represents in one line — the §3.3 "center delta" ask, kept out
        # of the donut centre so the % stays legible. Collect skips this:
        # the legend already explains the slice composition.
        delta = self._format_delta(snapshot)
        if delta:
            delta_lbl = QLabel(delta)
            delta_lbl.setObjectName("Sub")
            delta_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.layout().addWidget(delta_lbl)

        # Footer caption — 1px top separator visually splits donut area
        # from the explanation line (mockup .cap rule). Caption is left-
        # aligned per the mockup default (`.cap` has no text-align).
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        line_color = PALETTE[_palette_mode()]["line"]
        sep.setStyleSheet(  # pragma: no-qss — separator, token colour
            f"color: {line_color}; background: {line_color};"
        )
        sep.setFixedHeight(1)
        self.layout().addSpacing(2)
        self.layout().addWidget(sep)
        caption = QLabel(_PHASE_CAPTIONS.get(snapshot.key, ""))
        caption.setObjectName("Faint")
        caption.setWordWrap(True)
        caption.setAlignment(
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        )
        self.layout().addWidget(caption)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

    @staticmethod
    def _format_delta(snapshot: "PhaseSnapshot") -> str:
        """Tiny "remaining work" line beneath the donut. Collect skips
        (its legend already explains the slice composition). Phases with
        no captured denominator skip (the donut speaks 'Not started')."""
        if snapshot.key == "collect":
            return ""
        if snapshot.denominator <= 0:
            return ""
        remaining = snapshot.denominator - snapshot.numerator
        if remaining <= 0:
            return {
                "pick":   "All reviewed",
                "edit":   "All edited",
                "export": "All exported",
            }.get(snapshot.key, "Done")
        verb = {
            "pick":   "to review",
            "edit":   "to edit",
            "export": "to export",
        }.get(snapshot.key, "remaining")
        return f"{remaining:,} {verb}"

    @staticmethod
    def _wants_legend(snapshot: "PhaseSnapshot") -> bool:
        """Legend is for multi-slice phases (Collect). Pick/Edit/Share are
        numerator/track pairs — second slice is the inert remainder. Two
        slices alone never warrant a legend; three or more do."""
        non_track = [s for s in snapshot.slices if s.value > 0]
        return snapshot.key == "collect" and len(non_track) >= 2

    def mousePressEvent(self, e):  # noqa: N802
        super().mousePressEvent(e)
        self.activated.emit(self._snapshot.key)


class PhasesPage(QWidget):
    """Surface 03. Lands when an event is activated from the events list."""

    back_requested = pyqtSignal()
    phase_tile_activated = pyqtSignal(str)   # phase_key

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.gateway = gateway
        self._meta: EventMeta | None = None
        self._phases: list[PhaseSnapshot] = []
        self._build_ui()

    def _build_ui(self) -> None:
        # Back lives in the shared title bar, not in this page (Nelson 2026).
        self.uses_titlebar_back = True

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # Full-width surface identity rail — same top chrome as every main
        # surface (Nelson 2026); `home` tints it the brand accent (Phases is
        # an overview surface, not a phase).
        rail = QFrame()
        rail.setObjectName("SurfaceHeaderRail")
        rail.setProperty("phase", "home")
        rail.setFixedHeight(2)
        root.addWidget(rail)

        content = QWidget()
        outer = QVBoxLayout(content)
        outer.setContentsMargins(32, 18, 32, 24)
        outer.setSpacing(12)
        root.addWidget(content, 1)

        # ── TOP content band: event header + meta + summary ──
        # The shared #SurfaceBand look so nothing floats loose on the page.
        top_band = QFrame()
        top_band.setObjectName("SurfaceBand")
        top_l = QVBoxLayout(top_band)
        top_l.setContentsMargins(16, 14, 16, 14)
        top_l.setSpacing(10)

        # Header row: Tile · Title · stretch · StatusPill. Back now lives in the
        # shared title bar (uses_titlebar_back), not in the page.
        head_row = QHBoxLayout()
        head_row.setSpacing(14)
        self._tile_slot = QHBoxLayout()
        head_row.addLayout(self._tile_slot)
        self._title = QLabel("—")
        self._title.setObjectName("EventTitle")
        title_font = QFont(self._title.font())
        title_font.setPointSizeF(max(title_font.pointSizeF(), 18.0))
        title_font.setPixelSize(24)
        title_font.setWeight(QFont.Weight.Black)
        title_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, -0.5)
        self._title.setFont(title_font)
        head_row.addWidget(self._title)
        head_row.addStretch()
        top_l.addLayout(head_row)

        # ── Meta line (second row, indented under title) ──
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(0)
        # Indent the meta line under the title (tile width + spacing). Back is
        # gone from the header now, so this is smaller than before.
        self._meta_indent = 58
        meta_row.addSpacing(self._meta_indent)
        self._meta_line = QLabel("")
        self._meta_line.setObjectName("Sub")
        self._meta_line.setTextFormat(Qt.TextFormat.RichText)
        meta_row.addWidget(self._meta_line, 1)
        top_l.addLayout(meta_row)

        # ── Hero summary metric banner — four phase totals on one analytic
        # line. Hidden until the snapshots populate; redraw lives in _render.
        self._hero = Card2(padded=False)
        self._hero.setObjectName("PhasesHero")
        hero_l = QHBoxLayout(self._hero)
        hero_l.setContentsMargins(18, 12, 18, 12)
        hero_l.setSpacing(14)
        self._hero_line = QLabel("")
        self._hero_line.setObjectName("Sub")
        self._hero_line.setTextFormat(Qt.TextFormat.RichText)
        self._hero_line.setWordWrap(True)
        hero_l.addWidget(self._hero_line, 1)
        self._hero.setVisible(False)
        top_l.addWidget(self._hero)

        outer.addWidget(top_band)
        outer.addSpacing(8)

        # ── BOTTOM content band: the 2x2 phase grid ──
        bottom_band = QFrame()
        bottom_band.setObjectName("SurfaceBand")
        bottom_l = QVBoxLayout(bottom_band)
        bottom_l.setContentsMargins(16, 16, 16, 16)
        bottom_l.setSpacing(0)
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(18)
        self._grid.setVerticalSpacing(18)
        bottom_l.addWidget(self._grid_host)
        outer.addWidget(bottom_band)

        # The bands hug their content at the top; the phase cards stay compact
        # rather than stretching to fill the page (Nelson 2026).
        outer.addStretch(1)

    # ── data ────────────────────────────────────────────────────────────

    def setEventForPreview(
        self, meta: EventMeta, phases: list[PhaseSnapshot]
    ) -> None:
        """Wire mock data directly — used by the smoke and tests."""
        self._meta = meta
        self._phases = phases
        self._render()

    def set_event(self, event_id: str) -> bool:
        """Open ``event_id`` via the gateway, compute the four phase
        snapshots, render. Returns ``False`` if the gateway open fails
        (event deleted, db corrupt) — caller is expected to route the
        user back to the events list.

        Public surface mirrors the legacy
        :meth:`ActivityDashboardPage.set_event` so MainWindow keeps its
        existing call.
        """
        if self.gateway is None:
            return False
        try:
            eg = self.gateway.open_event(event_id)
        except Exception:                                          # noqa: BLE001
            log.exception("PhasesPage: cannot open event %s", event_id)
            return False
        try:
            event = eg.event()
            trip_days = eg.trip_days()
            day_tree = eg.day_tree()
            from mira import overview_stats
            photo_secs = self.gateway.settings.load() \
                .slideshow_seconds_per_slide_short
            per_camera = dict(overview_stats.captured_per_camera_time_share(
                eg, photo_seconds=photo_secs,
            ))
            # spec/66 phase totals, computed directly (the funnel's "edit"
            # bucket is really edit_exported, and "share" is the dead
            # share_tag — neither matches the Collect/Pick/Edit/Export model):
            #   Pick   = picked keepers; Edit = developed (has an adjustment);
            #   Export = exported files. Edit/Export are shown against picked.
            captured_total = len(eg.items(provenance="captured"))
            decided_total = eg.phase_decided_count("pick")   # reviewed
            picked_total = eg.phase_picked_count("pick")      # keepers
            developed_total = len(eg.adjustments())
            exported_total = len(eg.exported_item_ids())
        except Exception:                                          # noqa: BLE001
            log.exception("PhasesPage: gateway query failed for %s", event_id)
            eg.close()
            return False
        finally:
            try:
                eg.close()
            except Exception:                                      # noqa: BLE001
                pass

        # ── EventMeta ──
        from datetime import date as _date
        from collections import Counter
        offs = [d.tz_minutes for d in trip_days if d.tz_minutes is not None]
        tz_display = ""
        location = ""
        if offs:
            main = Counter(offs).most_common(1)[0][0]
            sign = "−" if main < 0 else "+"
            hh, mm = divmod(abs(int(main)), 60)
            tz_display = f"UTC{sign}{hh}:{mm:02d}"
            location = (trip_days[0].location or "") if trip_days else ""

        meta = EventMeta(
            event_id=event_id,
            name=event.name or "(unnamed event)",
            event_type=event.event_type or "unclassified",
            event_subtype=event.event_subtype,
            is_closed=bool(event.is_closed),
            total_days=len(trip_days),
            start_date=(event.start_date or "")[:10] if event.start_date else "",
            end_date=(event.end_date or "")[:10] if event.end_date else "",
            tz_display=tz_display,
            location=location,
        )

        # ── PhaseSnapshot[] from the spec/66 totals + per-camera ──
        # (captured_total / picked_total / developed_total / exported_total
        #  computed above, before the gateway closed.)
        p = PALETTE["dark"]  # color hexes are theme-stable enough for the
        # slices; the donut paints from the live palette at paintEvent
        # anyway, so theme toggles just rebuild the colors.

        snapshots: list[PhaseSnapshot] = []

        # Collect: per-camera contribution. Color ramp cycles through the
        # palette accents so each camera reads as distinct.
        camera_palette = [p["blue"], p["green"], p["amber"], p["accent"],
                          p["pink"], p["ink_soft"]]
        collect_slices = [
            DonutSlice(label, value, camera_palette[i % len(camera_palette)])
            for i, (label, value) in enumerate(per_camera.items())
        ]
        days_with_items = sum(
            1 for g in day_tree
            if g["day_number"] is not None and g["total"] > 0
        )
        collect_status = "done" if captured_total > 0 else "idle"
        snapshots.append(PhaseSnapshot(
            key="collect",
            label="Collect",
            status=collect_status,
            slices=collect_slices,
            center_text=f"{captured_total:,}" if captured_total else "",
            center_sub=(
                f"{days_with_items}/{len(trip_days)} days"
                if trip_days else ""
            ),
            state_word="" if captured_total else "Not started",
        ))

        # Pick = decided / captured (review completeness, spec/66); Edit =
        # developed / picked; Export = exported / picked (Edit & Export among
        # the picked keepers).
        snapshots.append(self._ratio_snapshot(
            "pick", "Pick", decided_total, captured_total, p,
        ))
        snapshots.append(self._ratio_snapshot(
            "edit", "Edit", developed_total, picked_total, p,
        ))
        snapshots.append(self._ratio_snapshot(
            "export", "Export", exported_total, picked_total, p,
        ))

        self.setEventForPreview(meta, snapshots)
        return True

    def _apply_closed_card_state(self, is_closed: bool) -> None:
        """Closed-event visual mute for the modification phases.

        Parity stub with legacy ActivityDashboardPage so MainWindow's
        call from the status-toggle handler still resolves. The legacy
        disabled the modification tiles (Pick / Edit). The redesign
        carries this through the StatusPill on the header row, which
        already shows Closed; tile-level disabling lands in a follow-up
        polish pass.
        """
        log.debug("PhasesPage closed-state visual mute (stub): %s", is_closed)

    @staticmethod
    def _ratio_snapshot(
        key: str, label: str, numerator: int, denominator: int,
        palette: dict[str, str],
    ) -> "PhaseSnapshot":
        """Compose a 2-slice donut snapshot (filled vs. remaining track)
        from a numerator/denominator pair. Status follows the same rule
        as the legacy ActivityDashboardPage. Numerator/denominator are
        stored on the snapshot so the hero banner reads them back even
        for empty-state phases (slices=[])."""
        if denominator <= 0:
            return PhaseSnapshot(
                key=key, label=label, status="idle", slices=[],
                state_word="Not started",
                numerator=max(0, numerator), denominator=max(0, denominator),
            )
        if numerator <= 0:
            return PhaseSnapshot(
                key=key, label=label, status="idle", slices=[],
                state_word="Not started",
                numerator=0, denominator=denominator,
            )
        pct = int(round(numerator / denominator * 100))
        # Phase-identity fill (spec/66): Pick accent · Edit amber · Export green.
        fill_color = palette[_PHASE_FILL_TOKEN.get(key, "amber")]
        if numerator >= denominator:
            status = "done"
        else:
            status = "prog"
        return PhaseSnapshot(
            key=key, label=label, status=status,
            slices=[
                DonutSlice(label, numerator, fill_color),
                DonutSlice("Remaining", denominator - numerator,
                           palette["track"]),
            ],
            center_text=f"{pct}%",
            center_sub=f"{numerator:,} / {denominator:,}",
            numerator=numerator, denominator=denominator,
        )

    # ── render ─────────────────────────────────────────────────────────

    def _render(self) -> None:
        if self._meta is None:
            return
        self._title.setText(self._meta.name)
        self._meta_line.setText(self._format_breadcrumb(self._meta))
        self._refresh_hero(self._phases)

        # Category tile (left of title block)
        while self._tile_slot.count():
            it = self._tile_slot.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        tile = _CategoryTile(
            self._meta.event_type, self._meta.event_subtype,
            dim=self._meta.is_closed,
        )
        self._tile_slot.addWidget(tile)

        # 2x2 phase grid
        while self._grid.count():
            it = self._grid.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        # Ensure ordering: collect/pick/edit/share. Pad with empty snapshots
        # if the caller omitted one.
        by_key = {p.key: p for p in self._phases}
        for idx, key in enumerate(_PHASE_ORDER):
            snap = by_key.get(key) or PhaseSnapshot(
                key=key,
                label=key.title(),
                status="idle",
                slices=[],
                state_word="Not started",
            )
            card = PhaseCard(idx + 1, snap)
            card.activated.connect(self.phase_tile_activated.emit)
            self._grid.addWidget(card, idx // 2, idx % 2)

    def _refresh_hero(self, snapshots: list[PhaseSnapshot]) -> None:
        """Build the analytic-banner line from the rendered snapshots and
        show / hide the band accordingly. Only renders when there's
        captured data — for a brand-new empty event the band sits dormant
        and the page just shows the four "Not started" donuts.

        Format mirrors the dashboard rhythm spec/65 §3.3 sketches:
        ``Reviewed N / Total (PCT%) · Edited N / Picked · Exported N /
        Picked · D of D days touched``. Phase-identity colours on the
        key tokens so the line ties to the per-card hues.
        """
        by_key = {p.key: p for p in snapshots}
        collect = by_key.get("collect")
        if collect is None or (
            not collect.center_text and not collect.slices
        ):
            self._hero.setVisible(False)
            return

        def _ratio(key: str) -> tuple[int, int]:
            snap = by_key.get(key)
            if snap is None:
                return 0, 0
            return snap.numerator, snap.denominator

        captured_total = self._sum_collect(collect)
        reviewed_n, reviewed_total = _ratio("pick")
        edited_n, picked_total = _ratio("edit")
        exported_n, _ = _ratio("export")
        days_touched, days_total = self._collect_days(collect)

        p = PALETTE[_palette_mode()]
        accent = p.get("accent", "#7c6cff")
        amber = p.get("amber", "#fbbf24")
        green = p.get("green", "#34d399")
        ink = p.get("ink", "#eef1f7")
        ink_soft = p.get("ink_soft", "#8b94a7")

        def _b(text: str, color: str) -> str:
            return f"<span style='color:{color};font-weight:700;'>{text}</span>"

        parts: list[str] = []
        if reviewed_total > 0:
            pct = int(round(reviewed_n / reviewed_total * 100))
            parts.append(
                f"Reviewed {_b(f'{reviewed_n:,} / {reviewed_total:,}', ink)} "
                f"<span style='color:{accent};'>({pct}%)</span>"
            )
        else:
            parts.append(
                f"Captured {_b(f'{captured_total:,}', ink)} "
                f"<span style='color:{ink_soft};'>· awaiting Pick</span>"
            )
        if picked_total > 0:
            parts.append(
                f"Edited "
                f"{_b(f'{edited_n:,} / {picked_total:,}', amber if edited_n else ink)}"
            )
            parts.append(
                f"Exported "
                f"{_b(f'{exported_n:,} / {picked_total:,}', green if exported_n else ink)}"
            )
        if days_total > 0:
            parts.append(
                f"{_b(f'{days_touched} of {days_total}', ink)} "
                f"<span style='color:{ink_soft};'>days touched</span>"
            )
        sep = (
            f"<span style='color:{ink_soft};'>&nbsp;·&nbsp;</span>"
        )
        self._hero_line.setText(sep.join(parts))
        self._hero.setVisible(True)

    @staticmethod
    def _sum_collect(snapshot: PhaseSnapshot) -> int:
        """Total captures = sum of the per-camera slice values."""
        if snapshot.state_word:
            return 0
        return int(sum(max(0.0, s.value) for s in snapshot.slices))

    @staticmethod
    def _collect_days(snapshot: PhaseSnapshot) -> tuple[int, int]:
        """Parse 'N/M days' out of Collect.center_sub; returns (0, 0) on
        an empty event so the hero banner can skip the trailing token."""
        sub = snapshot.center_sub or ""
        if "/" not in sub:
            return 0, 0
        # Expected shape: '3/3 days' from set_event.
        head = sub.split(" ", 1)[0]
        try:
            a, b = head.split("/", 1)
            return int(a), int(b)
        except ValueError:
            return 0, 0

    def _format_breadcrumb(self, meta: EventMeta) -> str:
        """Bold the key facts (type / subtype / days); rest in ink_soft."""
        bits = []
        if meta.event_type:
            bits.append(f"<b>{meta.event_type.title()}</b>")
        if meta.event_subtype:
            bits.append(f"<b>{meta.event_subtype}</b>")
        if meta.total_days:
            unit = "day" if meta.total_days == 1 else "days"
            bits.append(f"<b>{meta.total_days} {unit}</b>")
        if meta.start_date and meta.end_date:
            bits.append(f"{meta.start_date} → {meta.end_date}")
        elif meta.start_date:
            bits.append(meta.start_date)
        if meta.tz_display:
            bits.append(meta.tz_display)
        if meta.location:
            bits.append(meta.location)
        return " · ".join(bits)
