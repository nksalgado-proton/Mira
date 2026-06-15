"""Smoke for the exported indicator wiring (spec/59 §8 / spec/66 §1.2).

Builds a synthetic event with three picked photos, one of which has an
``Exported Media/`` lineage row + on-disk file, then screenshots the
:class:`DaysGridPage` showing the corner exported badge on that single
cell (the redesign replaced the diagonal watermark with the corner
badge in grids — the diagonal still rides the single-photo viewport).

Outputs::

    scripts/smoke_exported_indicator_dark.png
    scripts/smoke_exported_indicator_light.png

Toggling ``show_exported_watermark`` off makes the badge disappear; the
behaviour is pinned by ``test_days_grid_exported_ids_returns_*`` in
``tests/test_exported_watermark.py``.
"""
from __future__ import annotations

import itertools
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QFont, QImage, QPainter
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget


N_PHOTOS = 6


def _write_jpeg(path: Path, idx: int) -> None:
    img = QImage(320, 214, QImage.Format.Format_RGB32)
    img.fill(QColor.fromHsv((idx * 53) % 360, 130, 200))
    p = QPainter(img)
    p.setPen(QColor(20, 20, 20))
    for x in range(0, 320, 24):
        p.drawLine(x, 0, x, 214)
    p.setFont(QFont("Arial", 56, QFont.Weight.Bold))
    p.drawText(img.rect(), Qt.AlignmentFlag.AlignCenter, f"{idx}")
    p.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(str(path), "JPG", 90)


def _build_event(root: Path):
    from mira.gateway.event_gateway import EventGateway
    from mira.store import models as m
    from mira.store.repo import EventStore

    store = EventStore.create(root / "event.db", event_id="evt-smoke")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-smoke", name="Indicator smoke",
        created_at="t", updated_at="t")))
    store.upsert(m.Camera(camera_id="C1"))
    store.upsert(m.TripDay(
        day_number=1, date="2026-04-01", description="Smoke day"))
    for i in range(1, N_PHOTOS + 1):
        rel = f"Original Media/p{i}.jpg"
        _write_jpeg(root / rel, i)
        store.upsert(m.Item(
            id=f"p{i}", kind="photo", origin_relpath=rel,
            sha256=f"{i:064d}", byte_size=2000,
            materialized_at="t", materialized_phase="ingest",
            camera_id="C1", day_number=1, provenance="captured",
            capture_time_raw=f"2026-04-01T08:0{i}:00",
            capture_time_corrected=f"2026-04-01T08:0{i}:00",
            created_at="t",
        ))
        store.upsert(m.PhaseState(
            item_id=f"p{i}", phase="pick", state="picked"))
    eg = EventGateway(
        store, event_root=root, now=lambda: "t",
        new_id=lambda c=itertools.count(): f"id-{next(c)}")
    # Two shipped items — write the files + the lineage rows.
    ship = root / "Exported Media" / "Dia 1"
    ship.mkdir(parents=True, exist_ok=True)
    for shipped in ("p2", "p5"):
        dest = ship / f"{shipped}.jpg"
        _write_jpeg(dest, int(shipped[1:]))
        eg.record_lineage(m.Lineage(
            export_relpath=f"Exported Media/Dia 1/{shipped}.jpg",
            phase="edit", source_kind="item", source_item_id=shipped,
            recipe_json='{"look": "natural"}', exported_at="t"))
        eg.set_edit_exported(shipped, True)
    return store, eg


def main() -> int:
    from types import SimpleNamespace

    from mira.settings.model import Settings
    from mira.ui.pages.days_grid_page import DaysGridPage
    from mira.ui.theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    out_dir = _REPO / "scripts"

    with tempfile.TemporaryDirectory(prefix="mira_indicator_") as tmp:
        root = Path(tmp)
        store, eg = _build_event(root)

        def _build_page(mode: str) -> QWidget:
            apply_theme(app, mode)
            root_w = QWidget()
            root_w.setObjectName("RedesignRoot")
            root_w.resize(1280, 800)
            rl = QVBoxLayout(root_w)
            rl.setContentsMargins(0, 0, 0, 0)
            page = DaysGridPage(parent=root_w)
            # A duck-typed app gateway that hands back the open eg.
            page.gateway = SimpleNamespace(
                settings=SimpleNamespace(load=lambda: Settings()),
                open_event=lambda _id: eg,
            )
            assert page.open_for_day(
                "evt-smoke", 1,
                title="Smoke day", date_iso="2026-04-01", phase="pick")
            rl.addWidget(page)
            return root_w

        for mode in ("dark", "light"):
            host = _build_page(mode)
            host.show()
            for _ in range(60):
                app.processEvents()
            out = out_dir / f"smoke_exported_indicator_{mode}.png"
            host.grab().save(str(out), "PNG")
            print(f"wrote {out}")
            host.close()
            host.deleteLater()

        eg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
