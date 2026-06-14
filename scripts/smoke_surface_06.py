"""Real-asset screenshot smoke for Surface 06 (Days Grid).

Drives ``DaysGridPage`` against the live Gateway pointing at
``D:\\Photos\\_mira_events`` and renders the first day of the first
event that has captured photos + at least one real cluster. The full
live path runs — ``day_grid_cells`` (the spec/32 cell engine),
``cell_color_for_item`` for §5a borders, ``cluster_color`` for cluster
aggregates, ``photo_thumb_cache`` for on-disk 256-px JPEG thumbs — so
the screenshot reflects the production surface, not a parallel mock.

Spec/65 §3.6 wants the §2.3 patterns visible in REAL context:
* **Blurred-fill thumbnails** — needs real images (gradient placeholders
  never trigger the backdrop). The live gateway path covers this.
* **Cluster pile + badge + count** — needs real cluster data. The live
  gateway path covers this too.
* **Mixed-cluster yellow border + split chip** — requires a cluster
  whose members are partially decided. We force one synthetic mixed-
  cluster row at the top of the grid so the pattern always lands even
  when the chosen event happens to be all-uniform.

Run::

    python scripts/smoke_surface_06.py

Outputs::

    scripts/smoke_surface_06_dark.png
    scripts/smoke_surface_06_light.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QSize
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def _pick_event_with_clusters(gw, eg_open):
    """Return ``(event_id, event_name, day_number, day_title, day_date)``
    for the first event whose first non-empty day has at least one
    cluster cell. Falls back to "first day of first event" if no event
    has clusters (smoke still runs, just without the cluster patterns
    from live data — the synthetic row at the top still demonstrates
    them)."""
    from mira.picked import day_grid_cells

    rows = gw.list_events()
    fallback = None
    for row in rows:
        event_id = str(row["id"])
        try:
            eg = eg_open(event_id)
        except Exception:
            continue
        try:
            trip_days = sorted(
                d.day_number for d in eg.trip_days()
                if d.day_number is not None
            )
            for dn in trip_days:
                try:
                    cells = day_grid_cells(eg, dn, phase="pick")
                except Exception:
                    continue
                if not cells:
                    continue
                if fallback is None:
                    fallback = (event_id, str(row.get("name", "")),
                                 dn, _day_title(eg, dn), _day_date(eg, dn))
                if any(c.is_cluster for c in cells):
                    return (event_id, str(row.get("name", "")),
                            dn, _day_title(eg, dn), _day_date(eg, dn))
        finally:
            try:
                eg.close()
            except Exception:
                pass
    return fallback


def _day_title(eg, day_number):
    for d in eg.trip_days():
        if d.day_number == day_number:
            return d.description or f"Day {day_number}"
    return f"Day {day_number}"


def _day_date(eg, day_number):
    for d in eg.trip_days():
        if d.day_number == day_number:
            return str(d.date) if d.date else ""
    return ""


def _demo_pixmap(color: str, w: int = 196, h: int = 146) -> QPixmap:
    """A hand-painted vivid pixmap so the synthetic mixed-cluster row
    visibly demonstrates the blurred-fill + split-chip patterns even
    on machines that don't have the live event data. Used ONLY for
    the synthetic head row — real cells decode through photo_cache."""
    pm = QPixmap(w, h)
    pm.fill(QColor("#000000"))
    p = QPainter(pm)
    try:
        # Two vertical bands so the blurred-fill backdrop has something
        # interesting to extend from when the Thumb scales down.
        p.fillRect(0, 0, w // 2, h, QColor(color))
        p.fillRect(w // 2, 0, w - w // 2, h, QColor("#1f2937"))
    finally:
        p.end()
    return pm


def _build_demo_mixed_cluster_item():
    """Synthetic GridItem demonstrating the §5a mixed-cluster split chip
    + yellow border pattern that §3.6 says never landed in a real grid.
    Prepended to whatever live items the gateway returns so the
    screenshot ALWAYS carries the pattern."""
    from mira.ui.pages.days_grid_page import GridItem

    return GridItem(
        item_id="demo:mixed_cluster",
        item_kind="cluster",
        pixmap=_demo_pixmap("#a78bfa"),     # iris violet — vivid demo cover
        state="mixed",
        visited=True,
        exported=False,
        cluster_type="burst",
        cluster_count=5,
        cluster_split=(3, 2),
    )


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.gateway import Gateway
    from mira.ui.pages.days_grid_page import DaysGridPage
    from mira.ui.theme import apply_theme

    gw = Gateway()
    if gw.photos_base_path() is None:
        print("photos_base_path is not set; aborting smoke")
        return 1

    picked = _pick_event_with_clusters(gw, gw.open_event)
    if picked is None:
        print("no openable events with days; aborting smoke")
        return 1
    event_id, event_name, day_number, day_title, day_date = picked
    print(f"smoking against event {event_name!r} day {day_number} ({day_title})")

    out_dir = _REPO / "scripts"
    for mode in ("dark", "light"):
        apply_theme(app, mode)
        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(1280, 800)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        page = DaysGridPage(gateway=gw, parent=root)
        ok = page.open_for_day(
            event_id, day_number, title=day_title, date_iso=day_date,
        )
        if not ok:
            print(f"open_for_day failed for day {day_number}; aborting smoke")
            return 1
        # Prepend the synthetic mixed-cluster head row so §3.6 patterns
        # always render. Touches _items / _day_items in lockstep so a
        # subsequent re-render keeps the row.
        demo = _build_demo_mixed_cluster_item()
        page._items.insert(0, demo)
        page._day_items.insert(0, demo)
        page._update_counts()
        page._refresh()
        rl.addWidget(page)
        root.show()
        # The thumb-loader QTimer needs the event loop to TICK in real
        # time to fire (processEvents() alone won't advance the timer
        # by ~20 ms enough times to drain the queue). Drive the loader
        # directly: pull the on-disk 256-px JPEG thumbs synchronously
        # so the screenshot captures REAL photos (the §3.6 punch list
        # ask). The cache is in-process — this is the same path the
        # timer would run, just without the 20 ms intervals.
        deadline_drains = 0
        while page._thumb_pending and deadline_drains < 5000:
            page._load_some_thumbs()
            deadline_drains += 1
        for _ in range(10):
            app.processEvents()
        pm = root.grab()
        out = out_dir / f"smoke_surface_06_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        page.close_event()
        root.close()
        root.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
