"""Surface 03 — Phases (Collect / Pick / Edit / Share donut cards).

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
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
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
    Donut,
    DonutSlice,
    chip_closed,
    chip_done,
    chip_idle,
    chip_open,
    chip_prog,
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
    "pick":    "Share of captures the user picked.",
    "edit":    "Share of selections that have a finished export.",
    "share":   "Share of captures landing in a shared collection.",
}

_PHASE_ORDER = ("collect", "pick", "edit", "share")


def _status_chip(status: str) -> QLabel:
    return {
        "done":    lambda: chip_done("Done"),
        "prog":    lambda: chip_prog("In progress"),
        "idle":    lambda: chip_idle("Not started"),
        "skipped": lambda: chip_closed("Skipped"),
    }.get(status, lambda: chip_idle("Not started"))()


class _StepBadge(QLabel):
    """Small numbered chip — 1..4 in accent_soft / accent.

    Borderless per the surface-03 mockup (.step rule): the accent_soft
    backdrop carries enough visual weight without an outline. Square-ish
    rounded rect at 8px radius matches the mockup's `border-radius: 8px`.
    """

    def __init__(self, n: int) -> None:
        super().__init__(str(n))
        self.setFixedSize(24, 24)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(
            "background: #211f3a; color: #7c6cff;"
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
        swatch.setStyleSheet(
            f"background: {s.color}; border-radius: 3px;"
        )
        row.addWidget(swatch)
        name = QLabel(s.label)
        name.setObjectName("Sub")
        row.addWidget(name)
        row.addStretch()
        pct = QLabel(f"{int(round(s.value / total * 100))}%")
        pct.setStyleSheet(
            "color: #eef1f7; font-weight: 600;"
            " font-variant-numeric: tabular-nums;"
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
        self.setMinimumHeight(280)
        self.layout().setSpacing(10)

        # Top row: step badge + phase label + status chip
        head = QHBoxLayout()
        head.setSpacing(10)
        head.addWidget(_StepBadge(step))
        title = QLabel(snapshot.label)
        title.setObjectName("CardTitle")
        head.addWidget(title)
        head.addStretch()
        head.addWidget(_status_chip(snapshot.status))
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
            donut.setFixedSize(140, 140)
            body.addStretch()
            body.addWidget(donut, 0, Qt.AlignmentFlag.AlignCenter)
            body.addStretch()
        else:
            donut.setSlices(snapshot.slices)
            donut.setCenterText(snapshot.center_text, snapshot.center_sub)
            donut.setFixedSize(160, 160)
            # Multi-slice phases (Collect: per-camera) get a legend right
            # of the donut so the slice colors are legible. Single-fill
            # phases (Pick/Edit/Share: numerator vs track) skip the legend
            # and center the donut — the center "62% · 412/1084" already
            # speaks the metric, so empty space looks better symmetric.
            if self._wants_legend(snapshot):
                body.addWidget(donut, 0, Qt.AlignmentFlag.AlignCenter)
                body.addWidget(_DonutLegend(snapshot.slices), 1)
            else:
                body.addStretch()
                body.addWidget(donut, 0, Qt.AlignmentFlag.AlignCenter)
                body.addStretch()
        self.layout().addLayout(body, 1)

        # Footer caption — 1px top separator visually splits donut area
        # from the explanation line (mockup .cap rule).
        sep = QFrame()
        sep.setFrameShape(QFrame.Shape.HLine)
        sep.setStyleSheet("color: #262b38; background: #262b38;")
        sep.setFixedHeight(1)
        self.layout().addSpacing(2)
        self.layout().addWidget(sep)
        caption = QLabel(_PHASE_CAPTIONS.get(snapshot.key, ""))
        caption.setObjectName("Faint")
        caption.setWordWrap(True)
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.layout().addWidget(caption)

        self.setCursor(Qt.CursorShape.PointingHandCursor)

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
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 24, 32, 24)
        outer.setSpacing(14)

        # ── Header row: Back · Tile · Title · stretch · StatusPill ──
        # Per the surface-03 mockup .head row, the title stays alone on
        # this line; the meta breadcrumb sits on a second line below,
        # indented to align under the title (NOT under the Back button).
        head_row = QHBoxLayout()
        head_row.setSpacing(14)
        self._back = ghost_button("‹ Back")
        self._back.clicked.connect(self.back_requested.emit)
        head_row.addWidget(self._back)
        self._tile_slot = QHBoxLayout()
        head_row.addLayout(self._tile_slot)
        self._title = QLabel("—")
        self._title.setObjectName("CardTitle")
        head_row.addWidget(self._title)
        head_row.addStretch()
        self._status_slot = QHBoxLayout()
        head_row.addLayout(self._status_slot)
        outer.addLayout(head_row)

        # ── Meta line (second row, indented under title) ──
        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(0)
        # Indent: Back button(~76px) + spacing(14) + Tile(46) + spacing(14)
        #       ≈ 150px so the meta starts roughly under the title text.
        # _meta_indent is reused by _render when the tile/back widths shift.
        self._meta_indent = 150
        meta_row.addSpacing(self._meta_indent)
        self._meta_line = QLabel("")
        self._meta_line.setObjectName("Sub")
        self._meta_line.setTextFormat(Qt.TextFormat.RichText)
        meta_row.addWidget(self._meta_line, 1)
        outer.addLayout(meta_row)

        # ── 2x2 phase grid ──
        self._grid_host = QWidget()
        self._grid = QGridLayout(self._grid_host)
        self._grid.setContentsMargins(0, 8, 0, 0)
        self._grid.setHorizontalSpacing(18)
        self._grid.setVerticalSpacing(18)
        outer.addWidget(self._grid_host, 1)

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
            # 4-row funnel: (captured, picked, edited, shared). Defends
            # against pre-Slice-A funnels with extra rows (cull/select
            # collapse).
            from mira import overview_stats
            funnel = overview_stats.phase_funnel_breakdown(eg)
            photo_secs = self.gateway.settings.load() \
                .slideshow_seconds_per_slide_short
            per_camera = dict(overview_stats.captured_per_camera_time_share(
                eg, photo_seconds=photo_secs,
            ))
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

        # ── PhaseSnapshot[] from the funnel + per-camera ──
        captured_total = funnel[0][1] if funnel else 0
        picked_total = funnel[1][1] if len(funnel) >= 2 else 0
        edited_total = funnel[2][1] if len(funnel) >= 3 else 0
        share_total = funnel[3][1] if len(funnel) >= 4 else 0

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

        snapshots.append(self._ratio_snapshot(
            "pick", "Pick", picked_total, captured_total, p,
        ))
        snapshots.append(self._ratio_snapshot(
            "edit", "Edit", edited_total, captured_total, p,
        ))
        snapshots.append(self._ratio_snapshot(
            "share", "Share", share_total, captured_total, p,
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
        as the legacy ActivityDashboardPage."""
        if denominator <= 0:
            return PhaseSnapshot(
                key=key, label=label, status="idle", slices=[],
                state_word="Not started",
            )
        if numerator <= 0:
            return PhaseSnapshot(
                key=key, label=label, status="idle", slices=[],
                state_word="Not started",
            )
        pct = int(round(numerator / denominator * 100))
        fill_color = palette["green"] if key == "pick" else palette["amber"]
        if key == "share":
            fill_color = palette["accent"]
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
        )

    # ── render ─────────────────────────────────────────────────────────

    def _render(self) -> None:
        if self._meta is None:
            return
        self._title.setText(self._meta.name)
        self._meta_line.setText(self._format_breadcrumb(self._meta))

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

        # Status pill (right of header row)
        while self._status_slot.count():
            it = self._status_slot.takeAt(0)
            w = it.widget() if it else None
            if w is not None:
                w.deleteLater()
        pill = chip_closed("Closed") if self._meta.is_closed else chip_open("Open")
        self._status_slot.addWidget(pill, 0, Qt.AlignmentFlag.AlignVCenter)

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
