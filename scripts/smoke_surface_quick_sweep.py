"""Real-asset smoke for the redesigned Quick Sweep flow (spec/70 Phase
3 pivot — DaysLists → DaysGrid → viewer).

Drives the full standalone Quick Sweep route on a real source folder:

1. Open the redesigned DaysListsPage in paths mode (via
   ``setEventForPreview`` with snapshots built from a paths-mode PickDay
   list).
2. Activate a day → open DaysGridPage in paths mode (via ``setDay`` with
   GridItems built from the day's SourceItems).
3. Click an item → open the redesigned QuickSweepPage viewer with the
   day's items + the shared K/D ledger.
4. Drive the locked spec/63 §4 keymap on the viewer (P / X / Space / C
   / F10) and verify state lands in the ledger.
5. Screenshot each surface in dark + light themes.

Outputs::

    scripts/smoke_quick_sweep_days_lists_{dark,light}.png
    scripts/smoke_quick_sweep_days_grid_{dark,light}.png
    scripts/smoke_quick_sweep_viewer_{dark,light}.png
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def _pick_event_folder(gw) -> Path | None:
    rows = gw.list_events()
    for row in rows:
        event_id = str(row["id"])
        try:
            eg = gw.open_event(event_id)
        except Exception:
            continue
        try:
            event_root = Path(eg.event_root)
            for sub in ("Original Media", "Originals", "Original_Media"):
                cand = event_root / sub
                if cand.exists() and any(cand.rglob("*")):
                    return cand
        finally:
            try:
                eg.close()
            except Exception:
                pass
    return None


def _send_key(widget, key):
    QApplication.postEvent(
        widget,
        QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier),
    )
    QApplication.postEvent(
        widget,
        QKeyEvent(QEvent.Type.KeyRelease, key, Qt.KeyboardModifier.NoModifier),
    )
    for _ in range(3):
        QApplication.processEvents()


def _drain(n=12):
    for _ in range(n):
        QApplication.processEvents()


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.gateway import Gateway
    from mira.ui.pages.days_grid_page import DaysGridPage
    from mira.ui.pages.days_lists_page import DaysListsPage, DaySnapshot
    from mira.ui.pages.quick_sweep_page import QuickSweepPage
    from mira.ui.theme import apply_theme
    from core.fresh_source import read_source_items
    from mira.picked.quick_sweep_buckets import build_fast_days
    from core.cull_state import STATE_KEPT

    gw = Gateway()
    if gw.photos_base_path() is None:
        print("photos_base_path is not set; aborting smoke")
        return 1
    source = _pick_event_folder(gw)
    if source is None:
        print("no source folder with photos found; aborting smoke")
        return 1
    print(f"smoking against source folder: {source}")
    items = read_source_items(source)
    if not items:
        print("source folder has no photos / videos; aborting smoke")
        return 1
    print(f"  source items: {len(items)}")

    state_ledger: dict[Path, str] = {it.path: STATE_KEPT for it in items}
    days = build_fast_days(items, state_for=lambda p: state_ledger.get(p, STATE_KEPT))
    print(f"  days: {len(days)}")
    if not days:
        return 1

    # Helper: build DaySnapshots paths-mode.
    def build_snapshots():
        out = []
        for d in days:
            items_count = sum(len(b.items) for b in d.buckets)
            bucket_count = len(d.buckets)
            hours = [0] * 24
            for b in d.buckets:
                for ci in b.items:
                    ts = ci.capture_time_corrected
                    if not ts:
                        continue
                    try:
                        h = datetime.fromisoformat(ts).hour
                        if 0 <= h < 24:
                            hours[h] += 1
                    except (ValueError, TypeError):
                        continue
            label = d.label or f"Day {d.day_number}"
            date_iso = ""
            title = label
            if " — " in label:
                title, date_iso = label.split(" — ", 1)
            out.append(DaySnapshot(
                day_number=d.day_number,
                title=title.strip(),
                date_iso=date_iso.strip(),
                picked=items_count, skipped=0,
                buckets=bucket_count, items=items_count,
                capture_hours=hours,
            ))
        return out

    def build_grid_items(day_items):
        from mira.ui.pages.days_grid_page import GridItem
        out = []
        for it in day_items:
            out.append(GridItem(
                item_id=str(it.path),
                item_kind="photo",
                state="picked",
                visited=False, exported=False,
                _path=it.path,
            ))
        return out

    # Group items by day.
    items_by_day = {}
    for d in days:
        wanted = {Path(ci.item_id) for b in d.buckets for ci in b.items}
        items_by_day[d.day_number] = [
            it for it in items if it.path in wanted
        ]

    snapshots = build_snapshots()
    target_day = days[0].day_number
    day_items = items_by_day[target_day]
    print(f"  target day: {target_day}, items in day: {len(day_items)}")

    out_dir = _REPO / "scripts"
    rc = 0

    for mode in ("dark", "light"):
        apply_theme(app, mode)

        # ── DaysListsPage screenshot ──
        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(1280, 800)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        lists = DaysListsPage(gw, parent=root)
        lists.setEventForPreview("Quick Sweep — smoke", snapshots)
        rl.addWidget(lists)
        root.show()
        _drain(20)
        pm = root.grab()
        out = out_dir / f"smoke_quick_sweep_days_lists_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        root.close()
        root.deleteLater()

        # ── DaysGridPage screenshot ──
        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(1280, 800)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        grid = DaysGridPage(gw, parent=root)
        grid_items = build_grid_items(day_items)
        snap_for_day = next(s for s in snapshots if s.day_number == target_day)
        grid.setDay(
            target_day, snap_for_day.title, snap_for_day.date_iso,
            grid_items,
        )
        rl.addWidget(grid)
        root.show()
        # Pump the thumb loader so the cells show real photos.
        for _ in range(200):
            grid._load_some_thumbs()
        _drain(20)
        pm = root.grab()
        out = out_dir / f"smoke_quick_sweep_days_grid_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        root.close()
        root.deleteLater()

        # ── QuickSweepPage viewer screenshot + drive ──
        root = QWidget()
        root.setObjectName("RedesignRoot")
        root.resize(1280, 800)
        rl = QVBoxLayout(root)
        rl.setContentsMargins(0, 0, 0, 0)
        viewer = QuickSweepPage()
        viewer.load(day_items, start_index=0, state=state_ledger)
        rl.addWidget(viewer)
        root.show()
        if mode == "dark":
            # Drive locked keymap once in dark mode.
            target_path = day_items[0].path
            _send_key(viewer._viewport, Qt.Key.Key_P)
            after_pick = state_ledger.get(target_path)
            _send_key(viewer._viewport, Qt.Key.Key_X)
            after_skip = state_ledger.get(target_path)
            _send_key(viewer._viewport, Qt.Key.Key_Space)
            after_space = state_ledger.get(target_path)
            _send_key(viewer._viewport, Qt.Key.Key_C)
            after_cycle = state_ledger.get(target_path)
            print("--- viewer drive ---")
            print(f"  after_pick: {after_pick}")
            print(f"  after_skip: {after_skip}")
            print(f"  after_space: {after_space}")
            print(f"  after_cycle: {after_cycle}")
            fires = []
            viewer._viewport.set_truth_internal(False)
            viewer._viewport.truth_requested.connect(
                lambda: fires.append(True))
            _send_key(viewer._viewport, Qt.Key.Key_F10)
            print(f"  F10 fires: {bool(fires)}")
            viewer._viewport.set_truth_internal(True)
            if not all([
                after_pick == "kept",
                after_skip == "discarded",
                after_space == "kept",
                after_cycle == "discarded",
                bool(fires),
            ]):
                print("SMOKE FAILED — see observations")
                rc = 1
        _drain(20)
        pm = root.grab()
        out = out_dir / f"smoke_quick_sweep_viewer_{mode}.png"
        pm.save(str(out), "PNG")
        print(f"wrote {out}")
        root.close()
        root.deleteLater()
    return rc


if __name__ == "__main__":
    sys.exit(main())
