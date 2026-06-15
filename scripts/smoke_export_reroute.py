"""Smoke for the spec/68 §3 Export reroute — Days Grid in Export mode.

Builds a synthetic single-day event with 6 picked photos, two of which
are already shipped (lineage + on-disk JPEG under ``Exported Media/``).
Opens :class:`DaysGridPage` with ``phase="export"`` and screenshots in
both themes; this is what the user sees after clicking the Phases
Export tile and picking a day on Days Lists.

Pins visible in the eyeball:

* Green identity rail + ``EXPORT`` badge (spec/71, the closed
  Days-Grid-shared-with-Edit gets the Export host's chrome).
* The "↑ Export green" primary trigger replaces the
  "Start a new pass…" button.
* "Pick all" → "Export all", "Skip all" → "Drop all" relabels.
* Already-shipped cells (2 and 5) wear the corner "↑ Exported"
  badge — the indicator wired in Commit A.
* The grid is per-day (not the retired flat MVP).

Outputs::

    scripts/smoke_export_reroute_dark.png
    scripts/smoke_export_reroute_light.png
"""
from __future__ import annotations

import itertools
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

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

    store = EventStore.create(root / "event.db", event_id="evt-reroute")
    store.save_document(m.EventDocument(event=m.Event(
        uuid="evt-reroute", name="Export reroute smoke",
        created_at="t", updated_at="t")))
    store.upsert(m.Camera(camera_id="C1"))
    store.upsert(m.TripDay(
        day_number=1, date="2026-04-01", description="Reroute day"))
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
    from mira.settings.model import Settings
    from mira.ui.pages.days_grid_page import DaysGridPage
    from mira.ui.theme import apply_theme

    app = QApplication.instance() or QApplication(sys.argv)
    out_dir = _REPO / "scripts"

    with tempfile.TemporaryDirectory(prefix="mira_reroute_") as tmp:
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
            page.gateway = SimpleNamespace(
                settings=SimpleNamespace(load=lambda: Settings()),
                open_event=lambda _id: eg,
            )
            assert page.open_for_day(
                "evt-reroute", 1,
                title="Reroute day", date_iso="2026-04-01",
                phase="export")
            rl.addWidget(page)
            return root_w

        for mode in ("dark", "light"):
            host = _build_page(mode)
            host.show()
            for _ in range(60):
                app.processEvents()
            out = out_dir / f"smoke_export_reroute_{mode}.png"
            host.grab().save(str(out), "PNG")
            print(f"wrote {out}")
            host.close()
            host.deleteLater()

        eg.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
