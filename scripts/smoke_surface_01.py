"""Real-asset screenshot smoke for Surface 01 (Events list).

Renders the full ``EventsPage`` with a hand-built ``EventCardData`` fleet
that mirrors the surface-01-initial-app.html mockup deck (Pousada Salve
Floresta open, Inseto na Varanda closed, etc.) — closed cards' Carousel
loads real exported JPEGs from ``D:\\Photos\\_mira_events\\`` so the
blurred-fill backdrop, cluster fills, and category-tile tuning all read
the way they would in production.

Spec/65 §6 says verification needs real assets — placeholder gradients
hide the patterns the design wants on stage. Run::

    python scripts/smoke_surface_01.py

Outputs::

    scripts/smoke_surface_01_dark.png
    scripts/smoke_surface_01_light.png
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication, QWidget

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


_PHOTOS = Path(r"D:\Photos\_mira_events")


def _load_pixmaps(folder: Path, n: int = 5) -> list[QPixmap]:
    """Load up to N JPEGs from a folder, capped at 480x320 for carousel use."""
    if not folder.is_dir():
        return []
    files = sorted(folder.glob("*.jpg"))[:n]
    out: list[QPixmap] = []
    for f in files:
        pm = QPixmap(str(f))
        if pm.isNull():
            continue
        pm = pm.scaled(
            480, 320,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        out.append(pm)
    return out


def _build_cards() -> tuple[list, dict[str, list[QPixmap]]]:
    from mira.ui.base.event_card import EventCardData
    from core.event_card_grid import STATUS_DONE, STATUS_IN_PROGRESS

    cards: list[EventCardData] = []
    samples: dict[str, list[QPixmap]] = {}

    # ── Open: Pousada Salve Floresta (Wildlife / Trip) ──
    pousada = EventCardData(
        event_id="ev-pousada",
        name="Pousada Salve Floresta",
        start_date=date(2026, 3, 29),
        end_date=date(2026, 3, 31),
        is_closed=False,
        total_days=3,
        event_type="trip",
        event_subtype="wildlife",
        description="",
        tags=[],
    )
    pousada.tz_display = "UTC−03:00"
    pousada.status_by_phase = {
        "collect": {1: STATUS_DONE, 2: STATUS_DONE, 3: STATUS_DONE},
        "pick":    {1: STATUS_IN_PROGRESS, 2: STATUS_IN_PROGRESS, 3: "not_started"},
        "edit":    {1: "not_started", 2: "not_started", 3: "not_started"},
        "export":  {1: "not_started", 2: "not_started", 3: "not_started"},
    }
    cards.append(pousada)

    # ── Open: Everest — Nepal (Mountains / Trip) ──
    everest = EventCardData(
        event_id="ev-everest",
        name="Everest — Nepal",
        start_date=date(2025, 10, 26),
        end_date=date(2025, 11, 4),
        is_closed=False,
        total_days=10,
        event_type="trip",
        event_subtype="mountains",
        description="",
        tags=[],
    )
    everest.tz_display = "UTC+05:45"
    everest.status_by_phase = {
        "collect": {i: STATUS_DONE for i in range(1, 11)},
        "pick":    {i: "not_started" for i in range(1, 11)},
        "edit":    {i: "not_started" for i in range(1, 11)},
        "export":  {i: "not_started" for i in range(1, 11)},
    }
    cards.append(everest)

    # ── Open: Região de Salta — Argentina (Road / Trip) ──
    salta = EventCardData(
        event_id="ev-salta",
        name="Região de Salta — Argentina",
        start_date=date(2025, 9, 27),
        end_date=date(2025, 10, 3),
        is_closed=False,
        total_days=7,
        event_type="trip",
        event_subtype="road",
        description="",
        tags=[],
    )
    salta.tz_display = "UTC−03:00"
    salta.status_by_phase = {
        "collect": {i: STATUS_DONE for i in range(1, 8)},
        "pick":    {1: STATUS_IN_PROGRESS, 2: STATUS_IN_PROGRESS, **{i: "not_started" for i in range(3, 8)}},
        "edit":    {i: "not_started" for i in range(1, 8)},
        "export":  {i: "not_started" for i in range(1, 8)},
    }
    cards.append(salta)

    # ── Closed: Inseto na Varanda (Macro / Session) ──
    inseto = EventCardData(
        event_id="ev-inseto",
        name="Inseto na Varanda",
        start_date=date(2026, 6, 3),
        end_date=date(2026, 6, 3),
        is_closed=True,
        total_days=1,
        event_type="session",
        event_subtype="macro",
        description="",
        tags=[],
    )
    inseto.collected_count = 56
    inseto.picked_count = 43
    inseto.edited_count = 21
    inseto.exported_count = 6
    inseto.classification_counts = {"macro": 32, "insect": 18, "leaf": 6}
    real_dir = _PHOTOS / "Inseto na Varanda" / "Edited Media" / \
        "Dia 1 — Varanda do Apartamento — 2026-06-03"
    samples["ev-inseto"] = _load_pixmaps(real_dir, n=5)
    cards.append(inseto)

    return cards, samples


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.ui.theme import apply_theme
    from mira.ui.pages.events_page import EventsPage

    cards, samples = _build_cards()
    out_dir = _REPO / "scripts"

    for mode in ("dark", "light"):
        apply_theme(app, mode)
        # Root host so the redesign gradient background renders behind the page.
        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(1100, 1380)
        from PyQt6.QtWidgets import QVBoxLayout
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)

        page = EventsPage(gateway=None, parent=root)
        page.setEventsForPreview(cards, sample_pixmaps_by_id=samples)
        rl.addWidget(page)

        root.show()
        # Let layout settle so EventsPage._header sub-line gets recomputed
        for _ in range(3):
            app.processEvents()
        pm = root.grab()
        out = out_dir / f"smoke_surface_01_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        root.close()
        root.deleteLater()

    return 0


if __name__ == "__main__":
    sys.exit(main())
