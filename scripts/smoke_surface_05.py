"""Real-asset screenshot smoke for Surface 05 (Days Lists).

Drives ``DaysListsPage`` against the live Gateway pointing at
``D:\\Photos\\_mira_events`` and opens **Pousada Salve Floresta** — a 3-day
wildlife trip whose phase_day_progress + cached_buckets feed real
per-day Pick/Skip counts and bucket-cache counts.

The page is set up via MainWindow's exact builder
(``_build_day_snapshots`` + ``_fill_capture_hours``) so this smoke
reproduces the production path, not a parallel mock. Spec/65 §6 wants
real assets — the capture-hour spark, the per-day skipped count, and
the bucket totals only read against real numbers.

Run::

    python scripts/smoke_surface_05.py

Outputs::

    scripts/smoke_surface_05_dark.png
    scripts/smoke_surface_05_light.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def _pick_event(gw):
    for row in gw.list_events():
        if "Pousada" in str(row.get("name", "")):
            return str(row["id"]), str(row.get("name", ""))
    # Fallback so the smoke runs on any machine.
    rows = gw.list_events()
    if rows:
        return str(rows[0]["id"]), str(rows[0].get("name", ""))
    raise RuntimeError("no events available")


def _build_snapshots_via_main_window(gw, event_id):
    """Reuse the MainWindow snapshot builder so the smoke walks the same
    code path as production (no parallel mock)."""
    from mira.ui.shell.main_window import MainWindow
    # Build a stand-in object exposing the same _build_day_snapshots /
    # _fill_capture_hours functions without instantiating the whole
    # MainWindow (which would require a QApplication ready + the menubar
    # built). The functions are class-bound but their work is gateway-
    # only, so we can call them with a tiny shim.
    shim = MainWindow.__new__(MainWindow)
    shim.gateway = gw
    snapshots = MainWindow._build_day_snapshots(shim, event_id)
    return snapshots


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.gateway import Gateway
    from mira.ui.pages.days_lists_page import DaysListsPage
    from mira.ui.theme import apply_theme

    gw = Gateway()
    if gw.photos_base_path() is None:
        print("photos_base_path is not set; aborting smoke")
        return 1
    event_id, event_name = _pick_event(gw)
    snapshots = _build_snapshots_via_main_window(gw, event_id)
    if snapshots is None:
        print(f"snapshot build failed for event_id={event_id!r}")
        return 1
    print(f"event {event_name!r} -> {len(snapshots)} day(s):")
    for s in snapshots:
        print(
            f"  day {s.day_number}: {s.title!r} · {s.picked} picked /"
            f" {s.skipped} skipped / {s.items} items / "
            f"{s.buckets} buckets"
        )

    out_dir = _REPO / "scripts"
    for mode in ("dark", "light"):
        apply_theme(app, mode)
        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(1180, 720)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        page = DaysListsPage(gateway=gw, parent=root)
        page.setEventForPreview(event_name, snapshots)
        rl.addWidget(page)
        root.show()
        for _ in range(3):
            app.processEvents()
        pm = root.grab()
        out = out_dir / f"smoke_surface_05_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        root.close()
        root.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
