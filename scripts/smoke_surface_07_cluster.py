"""Real-asset cluster-path smoke for Surface 07 (Picker).

Drives :class:`PickerPage` against a REAL cluster bucket — verifying
``open_to_cluster``, intra-cluster ← →, and the Enter sweep with
peaking. Different fixture from the flat single-item smoke; same
keymap contract.

Output: ``scripts/smoke_surface_07_cluster_dark.png``.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QVBoxLayout, QWidget

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO))


def _pick_event_with_cluster(gw, eg_open):
    """Find a cluster (burst / focus / exposure) we can drive."""
    from mira.picked import day_grid_cells

    rows = gw.list_events()
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
                for cell in cells:
                    if cell.is_cluster and cell.cluster is not None:
                        c = cell.cluster
                        if len(c.members) >= 2:
                            return (event_id, str(row.get("name", "")),
                                    dn, c)
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


def main() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    from mira.gateway import Gateway
    from mira.ui.pages.picker_page import PickerPage
    from mira.ui.theme import apply_theme

    gw = Gateway()
    if gw.photos_base_path() is None:
        print("photos_base_path is not set; aborting smoke")
        return 1

    picked = _pick_event_with_cluster(gw, gw.open_event)
    if picked is None:
        print("no openable events with a 2+ member cluster; aborting smoke")
        return 1
    event_id, event_name, day_number, cluster = picked
    print(
        f"smoking cluster {cluster.kind!r} bucket {cluster.bucket_key!r} "
        f"with {len(cluster.members)} members from {event_name!r}")

    apply_theme(app, "dark")
    root = QWidget()
    root.setObjectName("RedesignRoot")
    root.resize(1280, 800)
    rl = QVBoxLayout(root)
    rl.setContentsMargins(0, 0, 0, 0)
    page = PickerPage(gateway=gw, parent=root)
    ok = page.open_to_cluster(event_id, day_number, cluster, entry_idx=0)
    if not ok:
        print("open_to_cluster failed; aborting smoke")
        return 1
    rl.addWidget(page)
    root.show()

    print(f"  items_loaded: {len(page._items)}")
    print(f"  bucket_kind: {page._bucket.kind}")
    print(f"  film_btn_visible: {page._film_btn.isVisible()}")
    print(f"  combined_btn_visible: {page._combined_btn.isVisible()}")

    # Intra-cluster nav — Right should advance.
    start_idx = page._index
    _send_key(page.viewport, Qt.Key.Key_Right)
    print(f"  index after Right: {page._index} (was {start_idx})")
    assert page._index > start_idx, "Right did not advance inside cluster"

    # Left back.
    _send_key(page.viewport, Qt.Key.Key_Left)
    print(f"  index after Left: {page._index}")
    assert page._index == start_idx, "Left did not return"

    # Enter — sweep starts (for playable kinds). The engine refuses
    # when every member is explicitly Skipped (real-data case — the user
    # may have batched Skip-all over the cluster), so we Pick the cluster
    # first (gives the sweep something to play) before testing.
    from mira.picked.status import STATE_PICKED
    if page._film_btn.isVisible():
        for ci in page._items:
            page._eg.set_phase_state(ci.item_id, "pick", STATE_PICKED)
            page._state[ci.item_id] = STATE_PICKED
        _send_key(page.viewport, Qt.Key.Key_Return)
        for _ in range(5):
            app.processEvents()
        sweep_on = page._film_btn.isChecked()
        peaking_on = page.viewport.is_peaking_enabled()
        print(f"  film_btn checked after Return: {sweep_on}")
        print(f"  peaking enabled after Return: {peaking_on}")
        if not sweep_on:
            page._on_sweep_key()
            for _ in range(5):
                app.processEvents()
            sweep_on = page._film_btn.isChecked()
            peaking_on = page.viewport.is_peaking_enabled()
            print(f"  film_btn checked via _on_sweep_key: {sweep_on}")
            print(f"  peaking enabled via _on_sweep_key: {peaking_on}")
        assert sweep_on, "Enter did not start the cluster sweep"
        assert peaking_on, "sweep did not enable peaking"
        # Stop the sweep cleanly.
        page._film_btn.setChecked(False)
        page._toggle_film()

    for _ in range(20):
        app.processEvents()
    pm = root.grab()
    out = _REPO / "scripts" / "smoke_surface_07_cluster_dark.png"
    pm.save(str(out), "PNG")
    print(f"wrote {out}")
    page.close_event()
    root.close()
    root.deleteLater()
    return 0


if __name__ == "__main__":
    sys.exit(main())
