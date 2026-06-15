"""Real-asset smoke for the redesigned EditorPage (Surface 08).

Opens MainWindow against the "Inseto na Varanda" event, routes through
Phases → DaysLists → DaysGrid (in Edit phase) → EditorPage on a real
keeper item, then saves screenshots:

  * smoke_editor_page_<theme>_load.png — the surface immediately after
    bridge-load (browse pixels up, tools greyed during prep gap).
  * smoke_editor_page_<theme>_developed.png — after the prep worker
    delivers and the developed view replaces the browse.
  * smoke_editor_page_<theme>_lens.png — F10 developed-preview lens.

Run as ``python scripts/smoke_editor_page.py``; the screenshots land
next to the script. Designed to surface visual bugs before Nelson's
eyeball.
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

# Project root on sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QApplication

from mira.gateway import Gateway
from mira.ui.app import apply_font_scale
from mira.ui.shell.main_window import MainWindow
from mira.ui.theme import apply_theme

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
log = logging.getLogger("smoke_editor")


EVENT_ROOT = Path(r"D:\Photos\_mira_events\Inseto na Varanda")
OUT_DIR = Path(__file__).resolve().parent


def _grab(window: MainWindow, label: str) -> None:
    out = OUT_DIR / f"smoke_editor_page_{label}.png"
    pm: QPixmap = window.grab()
    pm.save(str(out))
    log.info("wrote %s (%d×%d)", out.name, pm.width(), pm.height())


def _pump(app: QApplication, ms: int) -> None:
    """Spin the event loop for ``ms`` so deferred work + queued signals
    get a chance to land. The prep worker is async, the viewport's
    decode worker is async."""
    deadline = time.monotonic() + ms / 1000.0
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app, "dark")
    apply_font_scale(app, 1.0)

    if not EVENT_ROOT.exists():
        log.error("event root not found: %s", EVENT_ROOT)
        return 1
    if not (EVENT_ROOT / "event.db").exists():
        log.error("no event.db under %s", EVENT_ROOT)
        return 1

    # Use the app gateway pointed at the real photos base path. The
    # Gateway() reads the photos_base_path from settings; we override
    # to make sure the smoke uses the dataset on disk regardless of
    # what the user has configured.
    gw = Gateway()
    gw.set_photos_base_path(str(EVENT_ROOT.parent))

    window = MainWindow(gateway=gw)
    window.resize(1600, 1000)
    window.show()
    _pump(app, 200)

    # Find the event id matching this root.
    event_id: str | None = None
    for row in gw.list_events():
        if (row.get("relpath") or "").endswith(EVENT_ROOT.name):
            event_id = str(row.get("id"))
            break
        if str(row.get("name") or "") == EVENT_ROOT.name:
            event_id = str(row.get("id"))
            break
    if event_id is None:
        log.error(
            "could not match event id for %s among %d events",
            EVENT_ROOT.name, len(gw.list_events()))
        return 1
    log.info("event id resolved: %s", event_id)

    # Open the event → phases dashboard. The redesigned route for Edit
    # phase is Phases → DaysLists → DaysGrid → EditorPage; we step it
    # manually so the smoke doesn't depend on the UI being clicked.
    window._open_event(event_id)
    _pump(app, 200)

    # Edit phase routing (the spec/70 Phase 3 §3 path).
    window._edit_phase_active = True
    window._open_days_lists_for(event_id)
    _pump(app, 200)
    _grab(window, "01_dayslists")

    # Find a day with at least one item.
    snapshots = window.days_lists_page._snapshots  # type: ignore[attr-defined]
    day_with_items = next(
        (s for s in snapshots if (s.picked + s.skipped) > 0
         or s.items > 0),
        snapshots[0] if snapshots else None)
    if day_with_items is None:
        log.error("no days with items")
        return 1
    log.info(
        "selected day %s — %d items",
        day_with_items.day_number, day_with_items.items)
    window._on_days_lists_day_activated(day_with_items.day_number)
    _pump(app, 400)
    _grab(window, "02_daysgrid")

    # Pick the first FLAT photo cell. Cluster covers carry an
    # ``item_kind == "cluster"`` and expand in place — we'd need to
    # drill in to reach a real item; for the smoke we look for a flat
    # photo first, then fall back to drilling into the first cluster
    # to grab its first member.
    items = window.days_grid_page._items
    first_photo = next(
        (it for it in items
         if getattr(it, "item_kind", None) == "photo"
         and getattr(it, "item_id", None)
         and not str(it.item_id).startswith("cluster:")),
        None)
    if first_photo is None:
        first_cluster = next(
            (it for it in items
             if getattr(it, "item_kind", None) == "cluster"
             and getattr(it, "_cull_cluster", None) is not None),
            None)
        if first_cluster is None:
            log.error(
                "no flat photo OR cluster in day %s",
                day_with_items.day_number)
            return 1
        log.info(
            "drilling into cluster %s to reach a member",
            first_cluster._cull_cluster.bucket_key)
        window.days_grid_page._open_cluster(first_cluster._cull_cluster)
        _pump(app, 200)
        sub = window.days_grid_page._items
        first_photo = next(
            (it for it in sub
             if getattr(it, "item_kind", None) == "photo"
             and getattr(it, "item_id", None)),
            None)
        if first_photo is None:
            log.error("cluster has no photo members?")
            return 1
    log.info("opening editor on item %s", first_photo.item_id)
    window._on_days_grid_item_activated(first_photo.item_id)
    _pump(app, 300)
    _grab(window, "03_editor_load")

    # Let the prep worker land the developed view.
    _pump(app, 4000)
    _grab(window, "04_editor_developed")

    # Cycle Look (L) to confirm the engine actually changes the pixmap.
    page = window.edit_page
    page._surface.cycle_look(1)
    _pump(app, 400)
    _grab(window, "05_editor_after_L")

    # F10 — the developed-preview lens.
    page._open_processed_lens()
    _pump(app, 800)
    _grab(window, "06_editor_F10_lens")

    # Close lens, back out cleanly.
    if hasattr(page, "_lens") and page._lens is not None:
        page._lens.close()
    _pump(app, 200)
    page._on_back()
    _pump(app, 200)
    _grab(window, "07_back_to_daysgrid")

    window.close()
    log.info("smoke complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
